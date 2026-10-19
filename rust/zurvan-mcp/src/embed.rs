//! Query embedding: candle BERT on the same `all-MiniLM-L6-v2` safetensors
//! the index was built with (design §4.4; spike: cosine 1.000000 vs
//! sentence-transformers on 70 queries), plus the mock hash the index may say
//! it holds.

use candle_core::{DType, Device, Tensor};
use candle_nn::VarBuilder;
use candle_transformers::models::bert::{BertModel, Config as BertConfig};
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use tokenizers::{PaddingParams, Tokenizer, TruncationParams, TruncationStrategy};

pub const MODEL: &str = "all-MiniLM-L6-v2";
pub const DIM: usize = 384;
/// sentence-transformers' `max_seq_length` for this model (its
/// sentence_bert_config.json). NOT tokenizer.json's 128.
pub const MAX_SEQ_LENGTH: usize = 256;

/// `scripts/embed.generate_mock_embedding`: SHA-256 bytes cycled to 384 dims,
/// `(b / 255) * 2 - 1` in f64, stored as f32 (vec_storage.encode packs f32).
pub fn mock(text: &str) -> Vec<f32> {
    let h = Sha256::digest(text.as_bytes());
    (0..DIM).map(|i| ((h[i % h.len()] as f64 / 255.0) * 2.0 - 1.0) as f32).collect()
}

/// Which query embedding an index calls for. The index is the source of
/// truth (hybrid_search._index_embedding_config), never the environment.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Space {
    Real,
    Mock,
    Unsupported(String),
}

impl Space {
    pub fn for_index(provider: Option<&str>, model: Option<&str>) -> Space {
        match (provider, model) {
            (Some("mock"), _) => Space::Mock,
            // embed.get_embedding maps a placeholder model name to the default.
            (Some("sentence_transformers"), Some(MODEL | "mock_model" | "deterministic_hash")) => Space::Real,
            (Some(p), m) => Space::Unsupported(format!("index embeddings are {p}/{}; this server embeds {MODEL} only", m.unwrap_or("None"))),
            (None, _) => Space::Unsupported("the index holds no embeddings".into()),
        }
    }
}

pub fn locate_model() -> Result<PathBuf, String> {
    locate_model_from(&|k| std::env::var(k).ok())
}

/// `ZURVAN_MODEL_DIR`, else the Hugging Face cache snapshot `refs/main` names.
pub fn locate_model_from(getenv: &dyn Fn(&str) -> Option<String>) -> Result<PathBuf, String> {
    let dir = match getenv("ZURVAN_MODEL_DIR").filter(|s| !s.is_empty()) {
        Some(d) => PathBuf::from(d),
        None => {
            let hf_home = getenv("HF_HOME")
                .filter(|s| !s.is_empty())
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from(getenv("HOME").unwrap_or_default()).join(".cache").join("huggingface"));
            let repo = hf_home.join("hub").join(format!("models--sentence-transformers--{MODEL}"));
            let snapshot = std::fs::read_to_string(repo.join("refs").join("main"))
                .map_err(|_| format!("model not found: no {MODEL} snapshot under {}", repo.display()))?;
            repo.join("snapshots").join(snapshot.trim())
        }
    };
    for f in ["config.json", "tokenizer.json", "model.safetensors"] {
        if !dir.join(f).is_file() {
            return Err(format!("model not found at {} (missing {f})", dir.display()));
        }
    }
    Ok(dir)
}

pub struct Embedder {
    model: BertModel,
    tokenizer: Tokenizer,
    device: Device,
}

impl Embedder {
    pub fn load(dir: &Path) -> Result<Self, String> {
        let device = Device::Cpu;
        let cfg: BertConfig = serde_json::from_str(&std::fs::read_to_string(dir.join("config.json")).map_err(|e| e.to_string())?)
            .map_err(|e| e.to_string())?;
        let mut tokenizer = Tokenizer::from_file(dir.join("tokenizer.json")).map_err(|e| e.to_string())?;
        tokenizer
            .with_truncation(Some(TruncationParams {
                max_length: MAX_SEQ_LENGTH,
                strategy: TruncationStrategy::LongestFirst,
                stride: 0,
                ..Default::default()
            }))
            .map_err(|e| e.to_string())?;
        tokenizer.with_padding(None::<PaddingParams>);
        // SAFETY: the safetensors file is read-only model weights; mmap'd, never written.
        let vb = unsafe { VarBuilder::from_mmaped_safetensors(&[dir.join("model.safetensors")], DType::F32, &device) }
            .map_err(|e| e.to_string())?;
        let model = BertModel::load(vb, &cfg).map_err(|e| e.to_string())?;
        Ok(Embedder { model, tokenizer, device })
    }

    pub fn token_count(&self, text: &str) -> usize {
        self.tokenizer.encode(text, true).map(|e| e.get_ids().len()).unwrap_or(0)
    }

    /// Transformer → attention-masked mean pooling → L2 normalise, as the
    /// model's sentence-transformers modules.json chains them.
    pub fn embed(&self, text: &str) -> Result<Vec<f32>, String> {
        let run = || -> candle_core::Result<Vec<f32>> {
            let enc = self.tokenizer.encode(text, true).map_err(|e| candle_core::Error::Msg(e.to_string()))?;
            let ids = Tensor::new(enc.get_ids(), &self.device)?.unsqueeze(0)?;
            let types = Tensor::new(enc.get_type_ids(), &self.device)?.unsqueeze(0)?;
            let mask = Tensor::new(enc.get_attention_mask(), &self.device)?.unsqueeze(0)?;
            let out = self.model.forward(&ids, &types, Some(&mask))?;
            let m = mask.to_dtype(DType::F32)?.unsqueeze(2)?;
            let summed = out.broadcast_mul(&m)?.sum(1)?;
            let count = m.sum(1)?.clamp(1e-9, f64::MAX)?;
            let mean = summed.broadcast_div(&count)?;
            let norm = mean.sqr()?.sum_keepdim(1)?.sqrt()?.clamp(1e-12, f64::MAX)?;
            mean.broadcast_div(&norm)?.squeeze(0)?.to_vec1::<f32>()
        };
        run().map_err(|e| e.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mock_matches_python() {
        let fx: serde_json::Value =
            serde_json::from_str(include_str!("../fixtures/mock_hello.json")).unwrap();
        let want: Vec<f32> = fx["vector"].as_array().unwrap().iter().map(|v| v.as_f64().unwrap() as f32).collect();
        assert_eq!(mock("hello"), want);
        assert_eq!(mock("hello").len(), 384);
    }

    #[test]
    fn space_follows_the_index() {
        assert_eq!(Space::for_index(Some("sentence_transformers"), Some("all-MiniLM-L6-v2")), Space::Real);
        assert_eq!(Space::for_index(Some("sentence_transformers"), Some("mock_model")), Space::Real);
        assert_eq!(Space::for_index(Some("mock"), Some("deterministic_hash")), Space::Mock);
        assert!(matches!(Space::for_index(Some("sentence_transformers"), Some("all-mpnet-base-v2")), Space::Unsupported(_)));
        assert!(matches!(Space::for_index(None, None), Space::Unsupported(_)));
    }

    #[test]
    fn missing_model_reports_unavailable() {
        let e = locate_model_from(&|k| (k == "ZURVAN_MODEL_DIR").then(|| "/nonexistent/zurvan-model".to_string())).unwrap_err();
        assert!(e.contains("/nonexistent/zurvan-model"), "{e}");
    }

    #[test]
    fn tokenizer_truncates_at_256_not_128() {
        let Ok(dir) = locate_model() else {
            eprintln!("skipping: all-MiniLM-L6-v2 not in the local Hugging Face cache");
            return;
        };
        let e = Embedder::load(&dir).unwrap();
        let long = "word ".repeat(300);
        assert_eq!(e.token_count(&long), 256);
        assert!(e.token_count("short query") < 10);
        let v = e.embed("how does the knowledge ledger mint a revision").unwrap();
        assert_eq!(v.len(), 384);
        let norm: f32 = v.iter().map(|x| x * x).sum::<f32>().sqrt();
        assert!((norm - 1.0).abs() < 1e-5, "L2-normalised, got {norm}");
    }
}
