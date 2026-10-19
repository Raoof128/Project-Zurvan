//! The vector cache (design §4.3): the index's float32 vectors as one
//! contiguous, mmap'd matrix per index GENERATION, so a full-scan cosine is a
//! sequential SIMD-friendly pass instead of 303k BLOB reads through SQLite.
//!
//! File `data/cache/vectors-<key>.zvec` (0600, in a 0700 dir — embeddings of
//! private pages are private): a 128-byte header (`ZVEC0001`, dim u32, 0u32,
//! rows u64, the 64-byte ASCII key), `rows × dim` f32 LE in `embeddings` rowid
//! order, `rows` chunk_ids as 64-byte ASCII, `rows` × i64 LE `chunks.rowid`
//! (FTS returns rowids: spike verdict). Built to a unique temp name, fsynced,
//! and renamed, so a reader never sees a partial file and racing builders end
//! with identical bytes. The map is file-backed page cache, not heap.

use crate::config::Config;
use crate::store::IndexMeta;
use memmap2::Mmap;
use rayon::prelude::*;
use rusqlite::Connection;
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::io::{BufWriter, Seek, SeekFrom, Write};
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

const MAGIC: &[u8; 8] = b"ZVEC0001";
const HEADER: usize = 128;
const ID_BYTES: usize = 64;
const BLOCK_ROWS: usize = 8192;
/// Keyword's share of the fused score (hybrid_search.KEYWORD_WEIGHT).
pub const KEYWORD_WEIGHT: f64 = 0.2;

static TMP_NONCE: AtomicU64 = AtomicU64::new(0);

/// A build marker older than this belongs to a dead builder.
const MARKER_STALE: Duration = Duration::from_secs(15 * 60);
/// The longest a server waits for another process's build before its own.
const BUILD_WAIT: Duration = Duration::from_secs(180);

/// Binds the cache to EVERYTHING that defines the vectors: the source
/// manifest, the published file's identity, its build time, and the embedding
/// space. (Plan said the manifest alone; two builds from one manifest can hold
/// different vectors — a mock rebuild would — so the generation is in the key.)
pub fn cache_key(meta: &IndexMeta) -> String {
    let g = meta.generation;
    let s = format!(
        "{}|{}|{}|{}|{}|{}|{}|{}",
        meta.built_from_manifest.as_deref().unwrap_or(""),
        g.dev,
        g.ino,
        g.size,
        g.mtime_ns,
        meta.built_at.as_deref().unwrap_or(""),
        meta.provider.as_deref().unwrap_or(""),
        meta.model.as_deref().unwrap_or(""),
    );
    Sha256::digest(s.as_bytes()).iter().map(|b| format!("{b:02x}")).collect()
}

fn cache_name(key: &str) -> String {
    format!("vectors-{key}.zvec")
}

fn cache_path(cfg: &Config, key: &str) -> PathBuf {
    cfg.cache_dir.join(cache_name(key))
}

/// Where a server that cannot write `data/cache/` keeps its cache: one
/// directory per user under the temp dir (per-user on macOS already).
pub fn default_fallback_dir() -> PathBuf {
    std::env::temp_dir().join("zurvan-mcp-vectors")
}

/// Make `dir` a private directory this account owns: created 0700, never a
/// symlink, and chmod-able (only the owner can chmod), so a directory planted
/// under a shared temp name by another account is refused, not used.
fn private_dir(dir: &Path) -> Result<(), String> {
    use std::os::unix::fs::PermissionsExt;
    match std::fs::DirBuilder::new().mode(0o700).create(dir) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(e) => return Err(e.to_string()),
    }
    let m = std::fs::symlink_metadata(dir).map_err(|e| e.to_string())?;
    if !m.file_type().is_dir() {
        return Err(format!("{} is not a private directory", dir.display()));
    }
    std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700)).map_err(|e| e.to_string())
}

/// Stream `embeddings` (with each chunk's rowid) into a new cache file in
/// the store's `data/cache/`.
pub fn build(cfg: &Config, conn: &Connection, key: &str, on_progress: &dyn Fn(u64, u64)) -> Result<PathBuf, String> {
    std::fs::DirBuilder::new().recursive(true).mode(0o700).create(&cfg.cache_dir).map_err(|e| e.to_string())?;
    // DirBuilder's mode applies only to directories it creates; an existing
    // cache dir (or one created under a permissive umask) is tightened here.
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&cfg.cache_dir, std::fs::Permissions::from_mode(0o700)).map_err(|e| e.to_string())?;
    }
    build_in(&cfg.cache_dir, conn, key, on_progress)
}

/// The cache file itself, written to a temp name in `dir`, fsynced, renamed.
fn build_in(dir: &Path, conn: &Connection, key: &str, on_progress: &dyn Fn(u64, u64)) -> Result<PathBuf, String> {
    let total: i64 = conn.query_row("SELECT count(*) FROM embeddings", [], |r| r.get(0)).map_err(|e| e.to_string())?;
    let final_path = dir.join(cache_name(key));
    let tmp = dir.join(format!(
        "vectors-{key}.zvec.tmp-{}-{}",
        std::process::id(),
        TMP_NONCE.fetch_add(1, Ordering::SeqCst)
    ));
    let result = (|| -> Result<(), String> {
        let file = std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(&tmp).map_err(|e| e.to_string())?;
        let mut w = BufWriter::with_capacity(8 << 20, file);
        w.write_all(&[0u8; HEADER]).map_err(|e| e.to_string())?;
        let mut st = conn
            .prepare("SELECT e.chunk_id, e.vector, c.rowid FROM embeddings e LEFT JOIN chunks c ON c.chunk_id = e.chunk_id ORDER BY e.rowid")
            .map_err(|e| e.to_string())?;
        let mut rows = st.query([]).map_err(|e| e.to_string())?;
        let (mut n, mut dim) = (0u64, 0usize);
        let mut ids: Vec<u8> = Vec::with_capacity(total as usize * ID_BYTES);
        let mut rowids: Vec<u8> = Vec::with_capacity(total as usize * 8);
        while let Some(r) = rows.next().map_err(|e| e.to_string())? {
            let id: String = r.get(0).map_err(|e| e.to_string())?;
            let blob = r.get_ref(1).map_err(|e| e.to_string())?.as_blob().map_err(|e| e.to_string())?;
            let rowid: Option<i64> = r.get(2).map_err(|e| e.to_string())?;
            if n == 0 {
                dim = blob.len() / 4;
            }
            if blob.len() != dim * 4 || blob.len() % 4 != 0 {
                return Err(format!("embedding {n} has {} bytes, expected {}", blob.len(), dim * 4));
            }
            if id.len() > ID_BYTES || !id.is_ascii() {
                return Err(format!("chunk_id at row {n} is not ASCII of at most {ID_BYTES} bytes"));
            }
            w.write_all(blob).map_err(|e| e.to_string())?;
            let mut padded = [0u8; ID_BYTES];
            padded[..id.len()].copy_from_slice(id.as_bytes());
            ids.extend_from_slice(&padded);
            rowids.extend_from_slice(&rowid.unwrap_or(-1).to_le_bytes());
            n += 1;
            if n % 10_000 == 0 {
                on_progress(n, total as u64);
            }
        }
        if n != total as u64 {
            return Err(format!("read {n} embeddings, expected {total}"));
        }
        w.write_all(&ids).map_err(|e| e.to_string())?;
        w.write_all(&rowids).map_err(|e| e.to_string())?;
        let mut header = [0u8; HEADER];
        header[..8].copy_from_slice(MAGIC);
        header[8..12].copy_from_slice(&(dim as u32).to_le_bytes());
        header[16..24].copy_from_slice(&n.to_le_bytes());
        header[24..24 + key.len().min(64)].copy_from_slice(&key.as_bytes()[..key.len().min(64)]);
        w.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
        w.write_all(&header).map_err(|e| e.to_string())?;
        let file = w.into_inner().map_err(|e| e.to_string())?;
        file.sync_all().map_err(|e| e.to_string())?;
        on_progress(n, total as u64);
        Ok(())
    })();
    match result {
        Ok(()) => {
            std::fs::rename(&tmp, &final_path).map_err(|e| e.to_string())?;
            Ok(final_path)
        }
        Err(e) => {
            let _ = std::fs::remove_file(&tmp);
            Err(e)
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub struct Ranked {
    pub score: f64,
    pub row: u32,
    pub semantic: f64,
    pub keyword: f64,
}

pub struct VectorCache {
    mmap: Mmap,
    key: String,
    dim: usize,
    rows: usize,
    norms: Vec<f32>,
    by_rowid: Vec<(i64, u32)>,
}

impl VectorCache {
    pub fn open(path: &Path, key: &str, expected_rows: u64) -> Result<Self, String> {
        let file = std::fs::File::open(path).map_err(|e| e.to_string())?;
        // SAFETY: the file is published by rename and never modified in place
        // (a rebuild writes a NEW file under a new key).
        let mmap = unsafe { Mmap::map(&file) }.map_err(|e| e.to_string())?;
        if mmap.len() < HEADER || &mmap[..8] != MAGIC {
            return Err("not a vector cache".into());
        }
        let dim = u32::from_le_bytes(mmap[8..12].try_into().unwrap()) as usize;
        let rows = u64::from_le_bytes(mmap[16..24].try_into().unwrap());
        let stored_key = String::from_utf8_lossy(&mmap[24..88]).trim_end_matches('\0').to_string();
        if stored_key != key {
            return Err("vector cache key mismatch".into());
        }
        if rows != expected_rows {
            return Err(format!("vector cache holds {rows} rows, index has {expected_rows}"));
        }
        let rows = rows as usize;
        if mmap.len() != HEADER + rows * dim * 4 + rows * ID_BYTES + rows * 8 {
            return Err("vector cache has the wrong length".into());
        }
        let mut cache = VectorCache { mmap, key: key.to_string(), dim, rows, norms: Vec::new(), by_rowid: Vec::new() };
        cache.norms = (0..rows).into_par_iter().map(|r| cache.vector(r as u32).iter().map(|x| x * x).sum::<f32>().sqrt()).collect();
        let base = HEADER + rows * dim * 4 + rows * ID_BYTES;
        let mut by_rowid: Vec<(i64, u32)> = (0..rows)
            .map(|r| (i64::from_le_bytes(cache.mmap[base + r * 8..base + r * 8 + 8].try_into().unwrap()), r as u32))
            .filter(|(rid, _)| *rid >= 0)
            .collect();
        by_rowid.sort_unstable();
        cache.by_rowid = by_rowid;
        Ok(cache)
    }

    pub fn key(&self) -> &str {
        &self.key
    }
    pub fn rows(&self) -> usize {
        self.rows
    }
    pub fn dim(&self) -> usize {
        self.dim
    }

    pub fn vector(&self, row: u32) -> &[f32] {
        let start = HEADER + row as usize * self.dim * 4;
        let bytes = &self.mmap[start..start + self.dim * 4];
        // SAFETY: HEADER is 128 and dim*4 is a multiple of 4, so every vector
        // starts 4-aligned within a page-aligned map; f32 has no invalid bits.
        unsafe { std::slice::from_raw_parts(bytes.as_ptr() as *const f32, self.dim) }
    }

    pub fn chunk_id(&self, row: u32) -> &str {
        let start = HEADER + self.rows * self.dim * 4 + row as usize * ID_BYTES;
        let raw = &self.mmap[start..start + ID_BYTES];
        let end = raw.iter().position(|b| *b == 0).unwrap_or(ID_BYTES);
        std::str::from_utf8(&raw[..end]).unwrap_or("")
    }

    pub fn row_for_rowid(&self, rowid: i64) -> Option<u32> {
        self.by_rowid.binary_search_by_key(&rowid, |(r, _)| *r).ok().map(|i| self.by_rowid[i].1)
    }

    /// `score DESC, chunk_id ASC` (hybrid_search._Ranked).
    fn outranks(&self, a: &Ranked, b: &Ranked) -> bool {
        if a.score != b.score {
            return a.score > b.score;
        }
        self.chunk_id(a.row) < self.chunk_id(b.row)
    }

    fn insert_bounded(&self, best: &mut Vec<Ranked>, r: Ranked, k: usize) {
        if best.len() == k && !self.outranks(&r, &best[k - 1]) {
            return;
        }
        let pos = best.partition_point(|x| self.outranks(x, &r));
        best.insert(pos, r);
        best.truncate(k);
    }

    /// Full scan (never KNN): every row gets a fused score, exactly as
    /// `_score_semantic_top_k`. Cosine is computed the way sqlite-vec does —
    /// f32 distance `1 - dot/(|q|·|r|)`, then similarity `1 - d` in f64 — and
    /// a NaN similarity maps to semantic 0, as Python's `max(0.0, nan)` does.
    pub fn top_k(&self, q: &[f32], keyword: &HashMap<u32, f64>, k: usize) -> Vec<Ranked> {
        if k == 0 || self.rows == 0 || q.len() != self.dim {
            return Vec::new();
        }
        let qn = q.iter().map(|x| x * x).sum::<f32>().sqrt();
        let blocks: Vec<Vec<Ranked>> = (0..self.rows.div_ceil(BLOCK_ROWS))
            .into_par_iter()
            .map(|b| {
                let mut best: Vec<Ranked> = Vec::with_capacity(k + 1);
                let end = ((b + 1) * BLOCK_ROWS).min(self.rows);
                for row in (b * BLOCK_ROWS)..end {
                    let v = self.vector(row as u32);
                    let mut dot = 0f32;
                    for (a, c) in v.iter().zip(q) {
                        dot += a * c;
                    }
                    let distance = 1.0f32 - dot / (qn * self.norms[row]);
                    let similarity = 1.0f64 - distance as f64;
                    let semantic = if similarity.is_nan() { 0.0 } else { ((similarity + 1.0) / 2.0).max(0.0) };
                    let kw = *keyword.get(&(row as u32)).unwrap_or(&0.0);
                    let r = Ranked { score: KEYWORD_WEIGHT * kw + (1.0 - KEYWORD_WEIGHT) * semantic, row: row as u32, semantic, keyword: kw };
                    self.insert_bounded(&mut best, r, k);
                }
                best
            })
            .collect();
        let mut best: Vec<Ranked> = Vec::with_capacity(k + 1);
        for r in blocks.into_iter().flatten() {
            self.insert_bounded(&mut best, r, k);
        }
        best
    }
}

/// Keep the newest `keep` caches; remove crash-leftover temp files older than an hour.
pub fn prune(cache_dir: &Path, keep: usize) {
    let Ok(rd) = std::fs::read_dir(cache_dir) else { return };
    let mut caches: Vec<(std::time::SystemTime, PathBuf)> = Vec::new();
    for e in rd.filter_map(Result::ok) {
        let name = e.file_name().to_string_lossy().into_owned();
        let mtime = e.metadata().and_then(|m| m.modified()).unwrap_or(std::time::UNIX_EPOCH);
        if name.contains(".zvec.tmp-") {
            if mtime.elapsed().is_ok_and(|d| d.as_secs() > 3600) {
                let _ = std::fs::remove_file(e.path());
            }
        } else if name.ends_with(".zvec") {
            caches.push((mtime, e.path()));
        }
    }
    caches.sort_by_key(|c| std::cmp::Reverse(c.0));
    for (_, p) in caches.into_iter().skip(keep) {
        let _ = std::fs::remove_file(p);
    }
}

/// The loaded cache for the index generation being served: reused while the
/// key matches; otherwise opened from disk or built (blocking the caller),
/// one builder at a time per process.
pub struct Loader {
    current: Mutex<Option<Arc<VectorCache>>>,
    building: Mutex<()>,
    /// Used only when `data/cache/` cannot be written (final review I3).
    fallback: Option<PathBuf>,
    builds: std::sync::atomic::AtomicUsize,
}

impl Default for Loader {
    fn default() -> Self {
        Loader::with_fallback(Some(default_fallback_dir()))
    }
}

impl Loader {
    /// `None` = the store's cache or nothing (`build-cache` must not succeed
    /// somewhere the server would not look first).
    pub fn with_fallback(fallback: Option<PathBuf>) -> Loader {
        Loader { current: Mutex::new(None), building: Mutex::new(()), fallback, builds: Default::default() }
    }

    /// Build the store's cache unless another PROCESS is already building the
    /// same key (final review minor 8: N servers each wrote ~490 MB per
    /// promote). The builder holds `vectors-<key>.zvec.building`, created
    /// O_EXCL; everyone else waits for the finished file. A marker older than
    /// `MARKER_STALE`, or a wait longer than `BUILD_WAIT`, is taken over.
    fn build_once(&self, cfg: &Config, conn: &Connection, key: &str, rows: u64, on_progress: &dyn Fn(u64, u64)) -> Result<PathBuf, String> {
        std::fs::DirBuilder::new().recursive(true).mode(0o700).create(&cfg.cache_dir).map_err(|e| e.to_string())?;
        let marker = cfg.cache_dir.join(format!("{}.building", cache_name(key)));
        let done = cache_path(cfg, key);
        let waited_since = Instant::now();
        loop {
            // The other builder may have finished since the last look.
            if VectorCache::open(&done, key, rows).is_ok() {
                return Ok(done);
            }
            match std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(&marker) {
                Ok(_) => {
                    // ...including between that look and taking the marker.
                    if VectorCache::open(&done, key, rows).is_ok() {
                        let _ = std::fs::remove_file(&marker);
                        return Ok(done);
                    }
                    self.builds.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                    let built = build(cfg, conn, key, on_progress);
                    let _ = std::fs::remove_file(&marker);
                    return built;
                }
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
                    let stale = std::fs::metadata(&marker)
                        .and_then(|m| m.modified())
                        .map(|t| t.elapsed().unwrap_or_default() > MARKER_STALE)
                        .unwrap_or(false);
                    if stale || waited_since.elapsed() > BUILD_WAIT {
                        let _ = std::fs::remove_file(&marker);
                        continue;
                    }
                    std::thread::sleep(Duration::from_millis(100));
                }
                Err(e) => return Err(e.to_string()),
            }
        }
    }

    /// Cache files this loader built (tests; doctor).
    pub fn builds(&self) -> usize {
        self.builds.load(std::sync::atomic::Ordering::SeqCst)
    }

    pub fn current(&self) -> Option<Arc<VectorCache>> {
        self.current.lock().unwrap().clone()
    }

    pub fn get(&self, cfg: &Config, meta: &IndexMeta, on_progress: &dyn Fn(u64, u64)) -> Result<Arc<VectorCache>, String> {
        let key = cache_key(meta);
        if let Some(c) = self.current().filter(|c| c.key() == key) {
            return Ok(c);
        }
        let _single_flight = self.building.lock().unwrap();
        if let Some(c) = self.current().filter(|c| c.key() == key) {
            return Ok(c);
        }
        let rows = meta.embeddings as u64;
        let cache = match VectorCache::open(&cache_path(cfg, &key), &key, rows) {
            Ok(c) => {
                // A dir created loose before the 0700 fix is tightened here too
                // (final review minor 5); best-effort, the file itself is 0600.
                use std::os::unix::fs::PermissionsExt;
                let _ = std::fs::set_permissions(&cfg.cache_dir, std::fs::Permissions::from_mode(0o700));
                c
            }
            Err(_) => match self
                .fallback
                .as_ref()
                .filter(|d| d.is_dir() && private_dir(d).is_ok())
                .and_then(|d| VectorCache::open(&d.join(cache_name(&key)), &key, rows).ok())
            {
                Some(c) => c,
                None => {
                    // Built from exactly the generation the key names (I2).
                    let conn = crate::store::open_at(&cfg.search_db, meta.generation)?;
                    let built = match self.build_once(cfg, &conn, &key, rows, on_progress) {
                        Ok(p) => p,
                        Err(e1) => {
                            let fix = format!(
                                "run `zurvan-mcp build-cache` as an account that can write {}",
                                cfg.cache_dir.display()
                            );
                            let Some(dir) = self.fallback.as_ref() else {
                                return Err(format!("cannot write {} ({e1}); {fix}", cfg.cache_dir.display()));
                            };
                            match private_dir(dir).and_then(|_| {
                                self.builds.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                                build_in(dir, &conn, &key, on_progress)
                            }) {
                                Ok(p) => {
                                    prune(dir, 2);
                                    p
                                }
                                Err(e2) => {
                                    return Err(format!(
                                        "cannot write {} ({e1}) or {} ({e2}); {fix}",
                                        cfg.cache_dir.display(),
                                        dir.display()
                                    ))
                                }
                            }
                        }
                    };
                    VectorCache::open(&built, &key, rows)?
                }
            },
        };
        prune(&cfg.cache_dir, 2);
        let cache = Arc::new(cache);
        *self.current.lock().unwrap() = Some(cache.clone());
        Ok(cache)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;
    use crate::store::{open_ro, MetaCache};
    use crate::testutil::{search_db, Chunk};
    use std::collections::HashMap;

    const DIM: usize = 384;

    fn lcg(seed: &mut u64) -> f32 {
        *seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        ((*seed >> 33) as f32 / (1u64 << 31) as f32) * 2.0 - 1.0
    }

    fn vectors(n: usize, seed: u64) -> Vec<Vec<f32>> {
        let mut s = seed;
        (0..n).map(|_| (0..DIM).map(|_| lcg(&mut s)).collect()).collect()
    }

    fn ids(n: usize) -> Vec<String> {
        // Not in id order: row order must come from `embeddings` rowid, not chunk_id.
        (0..n).map(|i| format!("{:064x}", (i * 7919) % 100_003)).collect()
    }

    fn build_db(root: &std::path::Path, vs: &[Vec<f32>], ids: &[String], revision: &str) -> Config {
        let cfg = Config::resolve_from(Some(root.to_path_buf()), |_| None);
        let chunks: Vec<Chunk> = vs
            .iter()
            .zip(ids)
            .map(|(v, id)| Chunk { chunk_id: id, source_path: "wiki/x.md", heading: None, text: "t", indexed_at: "2026-10-18T00:00:00", vector: v.clone() })
            .collect();
        search_db(&cfg.search_db, "sentence_transformers", "all-MiniLM-L6-v2", &chunks,
                  &[("built_from_revision", revision), ("built_at", "2026-10-18T08:23:19+00:00"), ("built_from_manifest", "m1")]);
        cfg
    }

    fn built(cfg: &Config) -> (VectorCache, String) {
        let meta = MetaCache::default().index(cfg).unwrap();
        let key = cache_key(&meta);
        let conn = open_ro(&cfg.search_db).unwrap();
        let path = build(cfg, &conn, &key, &|_, _| {}).unwrap();
        (VectorCache::open(&path, &key, meta.chunks as u64).unwrap(), key)
    }

    #[test]
    fn roundtrip_bytes() {
        let dir = tempfile::tempdir().unwrap();
        let (vs, idv) = (vectors(1000, 1), ids(1000));
        let cfg = build_db(dir.path(), &vs, &idv, "7");
        let (c, _) = built(&cfg);
        assert_eq!(c.rows(), 1000);
        for row in [0usize, 1, 499, 999] {
            assert_eq!(c.vector(row as u32), vs[row].as_slice());
            assert_eq!(c.chunk_id(row as u32), idv[row]);
        }
        // chunks.rowid of the fixture's i-th insert is i+1.
        assert_eq!(c.row_for_rowid(1), Some(0));
        assert_eq!(c.row_for_rowid(1000), Some(999));
        assert_eq!(c.row_for_rowid(5000), None);
    }

    /// Embeddings of private pages are private: the cache dir is 0700 and
    /// each cache file 0600, even when the directory already existed.
    #[test]
    fn cache_permissions_are_private() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let cfg = build_db(dir.path(), &vectors(10, 4), &ids(10), "7");
        std::fs::create_dir_all(&cfg.cache_dir).unwrap();
        std::fs::set_permissions(&cfg.cache_dir, std::fs::Permissions::from_mode(0o755)).unwrap();
        let _ = built(&cfg);
        let mode = |p: &std::path::Path| std::fs::metadata(p).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode(&cfg.cache_dir), 0o700);
        for e in std::fs::read_dir(&cfg.cache_dir).unwrap() {
            assert_eq!(mode(&e.unwrap().path()), 0o600);
        }
    }

    #[test]
    fn refuses_wrong_key_and_row_mismatch() {
        let dir = tempfile::tempdir().unwrap();
        let cfg = build_db(dir.path(), &vectors(50, 2), &ids(50), "7");
        let meta = MetaCache::default().index(&cfg).unwrap();
        let key = cache_key(&meta);
        let conn = open_ro(&cfg.search_db).unwrap();
        let path = build(&cfg, &conn, &key, &|_, _| {}).unwrap();
        assert!(VectorCache::open(&path, "some-other-key", 50).is_err());
        assert!(VectorCache::open(&path, &key, 51).is_err());
        assert!(VectorCache::open(&path, &key, 50).is_ok());
    }

    #[test]
    fn top_k_matches_bruteforce_f64() {
        let dir = tempfile::tempdir().unwrap();
        let (vs, idv) = (vectors(1000, 3), ids(1000));
        let cfg = build_db(dir.path(), &vs, &idv, "7");
        let (c, _) = built(&cfg);
        let q = vectors(1, 99).remove(0);
        let keyword: HashMap<u32, f64> = [(3u32, 1.0), (10, 0.5), (700, 0.25)].into_iter().collect();
        let got = c.top_k(&q, &keyword, 10);

        let norm = |v: &[f32]| v.iter().map(|x| (*x as f64).powi(2)).sum::<f64>().sqrt();
        let mut brute: Vec<(f64, String)> = vs
            .iter()
            .enumerate()
            .map(|(i, v)| {
                let cos = v.iter().zip(&q).map(|(a, b)| *a as f64 * *b as f64).sum::<f64>() / (norm(v) * norm(&q));
                let sem = ((cos + 1.0) / 2.0).max(0.0);
                let kw = *keyword.get(&(i as u32)).unwrap_or(&0.0);
                (0.2 * kw + 0.8 * sem, idv[i].clone())
            })
            .collect();
        brute.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap().then_with(|| a.1.cmp(&b.1)));
        let got_ids: Vec<&str> = got.iter().map(|r| c.chunk_id(r.row)).collect();
        let want_ids: Vec<&str> = brute.iter().take(10).map(|b| b.1.as_str()).collect();
        assert_eq!(got_ids, want_ids);
        for (r, b) in got.iter().zip(&brute) {
            assert!((r.score - b.0).abs() < 1e-6, "{} vs {}", r.score, b.0);
        }
        assert!(got.iter().any(|r| r.keyword > 0.0) || !keyword.keys().any(|k| got.iter().any(|r| r.row == *k)));
    }

    #[test]
    fn ties_break_by_chunk_id() {
        let dir = tempfile::tempdir().unwrap();
        let v = vectors(1, 5).remove(0);
        // Five identical vectors: equal scores, so only chunk_id can order them.
        let idv: Vec<String> = ["e", "b", "d", "a", "c"].iter().map(|s| format!("{s:0>64}")).collect();
        let cfg = build_db(dir.path(), &vec![v.clone(); 5], &idv, "7");
        let (c, _) = built(&cfg);
        let got: Vec<&str> = c.top_k(&v, &HashMap::new(), 3).iter().map(|r| c.chunk_id(r.row)).collect();
        assert_eq!(got, vec![idv[3].as_str(), idv[1].as_str(), idv[4].as_str()]); // a, b, c
    }

    fn chmod(p: &std::path::Path, mode: u32) {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(p, std::fs::Permissions::from_mode(mode)).unwrap();
    }

    /// Final review 2026-10-19 (I3): the Python server searched a store it
    /// could not write; Rust lost semantic search whenever `data/cache/` was
    /// not writable (a sandboxed session). It now builds a private cache in
    /// the user's temp directory instead, and never writes the store.
    #[test]
    fn unwritable_cache_dir_falls_back_to_a_private_temp_cache() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let cfg = build_db(dir.path(), &vectors(12, 9), &ids(12), "7");
        let meta = MetaCache::default().index(&cfg).unwrap();
        let data = cfg.cache_dir.parent().unwrap().to_path_buf();
        chmod(&data, 0o555);
        let tmp = tempfile::tempdir().unwrap();
        let fallback = tmp.path().join("zurvan-mcp-vectors");
        let got = Loader::with_fallback(Some(fallback.clone())).get(&cfg, &meta, &|_, _| {});
        chmod(&data, 0o755);
        let cache = got.expect("served from the fallback cache");
        assert_eq!(cache.rows(), 12);
        assert!(!cfg.cache_dir.exists(), "the store was not written");
        assert_eq!(std::fs::metadata(&fallback).unwrap().permissions().mode() & 0o777, 0o700);
        let file = fallback.join(format!("vectors-{}.zvec", cache.key()));
        assert_eq!(std::fs::metadata(&file).unwrap().permissions().mode() & 0o777, 0o600);
    }

    #[test]
    fn cache_failure_names_the_path_and_the_fix() {
        let dir = tempfile::tempdir().unwrap();
        let cfg = build_db(dir.path(), &vectors(4, 3), &ids(4), "7");
        let meta = MetaCache::default().index(&cfg).unwrap();
        let data = cfg.cache_dir.parent().unwrap().to_path_buf();
        chmod(&data, 0o555);
        let tmp = tempfile::tempdir().unwrap();
        chmod(tmp.path(), 0o555);
        let got = Loader::with_fallback(Some(tmp.path().join("zurvan-mcp-vectors"))).get(&cfg, &meta, &|_, _| {});
        chmod(&data, 0o755);
        chmod(tmp.path(), 0o755);
        let err = got.err().expect("nowhere writable");
        assert!(err.contains(&cfg.cache_dir.display().to_string()), "{err}");
        assert!(err.contains("zurvan-mcp build-cache"), "{err}");
    }

    #[test]
    fn a_fallback_dir_owned_by_someone_else_is_refused() {
        // /tmp is shared on Linux: a directory planted under the fallback name
        // by another account must never hold our cache. A directory we do not
        // own is simulated by one that is not a directory at all.
        let dir = tempfile::tempdir().unwrap();
        let cfg = build_db(dir.path(), &vectors(4, 3), &ids(4), "7");
        let meta = MetaCache::default().index(&cfg).unwrap();
        let data = cfg.cache_dir.parent().unwrap().to_path_buf();
        chmod(&data, 0o555);
        let tmp = tempfile::tempdir().unwrap();
        let planted = tmp.path().join("zurvan-mcp-vectors");
        std::os::unix::fs::symlink(tmp.path(), &planted).unwrap();
        let got = Loader::with_fallback(Some(planted)).get(&cfg, &meta, &|_, _| {});
        chmod(&data, 0o755);
        assert!(got.is_err(), "a symlinked fallback dir is refused");
    }

    #[test]
    fn new_generation_triggers_rebuild() {
        let dir = tempfile::tempdir().unwrap();
        let cfg = build_db(dir.path(), &vectors(20, 6), &ids(20), "7");
        let metas = MetaCache::default();
        let loader = Loader::default();
        let a = loader.get(&cfg, &metas.index(&cfg).unwrap(), &|_, _| {}).unwrap();
        let again = loader.get(&cfg, &metas.index(&cfg).unwrap(), &|_, _| {}).unwrap();
        assert!(Arc::ptr_eq(&a, &again), "same generation reuses the loaded map");

        let other = tempfile::tempdir().unwrap();
        let next = build_db(other.path(), &vectors(30, 7), &ids(30), "8");
        std::fs::rename(&next.search_db, &cfg.search_db).unwrap();
        let b = loader.get(&cfg, &metas.index(&cfg).unwrap(), &|_, _| {}).unwrap();
        assert_eq!(b.rows(), 30);
        assert_ne!(a.key(), b.key());
        let files = std::fs::read_dir(&cfg.cache_dir).unwrap().filter(|e| e.as_ref().unwrap().path().extension().is_some_and(|x| x == "zvec")).count();
        assert_eq!(files, 2);
    }

    #[test]
    fn concurrent_builders_agree() {
        let dir = tempfile::tempdir().unwrap();
        let cfg = build_db(dir.path(), &vectors(300, 8), &ids(300), "7");
        let meta = MetaCache::default().index(&cfg).unwrap();
        let key = cache_key(&meta);
        let paths: Vec<std::path::PathBuf> = std::thread::scope(|s| {
            let hs: Vec<_> = (0..4)
                .map(|_| s.spawn(|| build(&cfg, &open_ro(&cfg.search_db).unwrap(), &key, &|_, _| {}).unwrap()))
                .collect();
            hs.into_iter().map(|h| h.join().unwrap()).collect()
        });
        assert!(paths.windows(2).all(|w| w[0] == w[1]));
        let raced = std::fs::read(&paths[0]).unwrap();
        // A single build elsewhere produces byte-identical output.
        let solo_dir = tempfile::tempdir().unwrap();
        let solo_cfg = Config { cache_dir: solo_dir.path().join("cache"), ..cfg.clone() };
        let solo = build(&solo_cfg, &open_ro(&cfg.search_db).unwrap(), &key, &|_, _| {}).unwrap();
        assert_eq!(raced, std::fs::read(solo).unwrap());
        let leftovers = std::fs::read_dir(&cfg.cache_dir).unwrap().filter(|e| e.as_ref().unwrap().file_name().to_string_lossy().contains(".tmp-")).count();
        assert_eq!(leftovers, 0, "no temp files survive");
    }

    #[test]
    fn prune_keeps_two() {
        let dir = tempfile::tempdir().unwrap();
        let d = dir.path();
        for (i, name) in ["vectors-a.zvec", "vectors-b.zvec", "vectors-c.zvec", "vectors-d.zvec"].iter().enumerate() {
            let p = d.join(name);
            std::fs::write(&p, b"x").unwrap();
            let t = std::time::SystemTime::UNIX_EPOCH + std::time::Duration::from_secs(1_000_000 + i as u64 * 10);
            std::fs::File::options().write(true).open(&p).unwrap().set_modified(t).unwrap();
        }
        std::fs::write(d.join("unrelated.txt"), b"keep").unwrap();
        prune(d, 2);
        let mut left: Vec<String> = std::fs::read_dir(d).unwrap().map(|e| e.unwrap().file_name().to_string_lossy().into_owned()).collect();
        left.sort();
        assert_eq!(left, vec!["unrelated.txt", "vectors-c.zvec", "vectors-d.zvec"]);
    }

    /// Final review minor 5: only `build` tightened the cache dir, so a dir
    /// created loose (0755) before that fix stayed listable by other accounts.
    #[test]
    fn opening_an_existing_cache_tightens_a_loose_cache_dir() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let cfg = build_db(dir.path(), &vectors(6, 1), &ids(6), "7");
        let meta = MetaCache::default().index(&cfg).unwrap();
        Loader::with_fallback(None).get(&cfg, &meta, &|_, _| {}).unwrap();
        chmod(&cfg.cache_dir, 0o755);
        let again = Loader::with_fallback(None);
        again.get(&cfg, &meta, &|_, _| {}).unwrap();
        assert_eq!(again.builds(), 0, "opened, not rebuilt");
        assert_eq!(std::fs::metadata(&cfg.cache_dir).unwrap().permissions().mode() & 0o777, 0o700);
    }

    /// Final review minor 8: every server rebuilt the ~490 MB cache after each
    /// promote. A build in progress in another process (its marker) is now
    /// waited for, not repeated.
    #[test]
    fn a_build_in_progress_elsewhere_is_waited_for_not_repeated() {
        let dir = tempfile::tempdir().unwrap();
        let cfg = build_db(dir.path(), &vectors(8, 2), &ids(8), "7");
        let meta = MetaCache::default().index(&cfg).unwrap();
        let key = cache_key(&meta);
        std::fs::create_dir_all(&cfg.cache_dir).unwrap();
        let marker = cfg.cache_dir.join(format!("vectors-{key}.zvec.building"));
        std::fs::write(&marker, b"other process").unwrap();
        let loader = Loader::with_fallback(None);
        std::thread::scope(|s| {
            s.spawn(|| {
                std::thread::sleep(std::time::Duration::from_millis(400));
                build_in(&cfg.cache_dir, &open_ro(&cfg.search_db).unwrap(), &key, &|_, _| {}).unwrap();
                std::fs::remove_file(&marker).unwrap();
            });
            let c = loader.get(&cfg, &meta, &|_, _| {}).unwrap();
            assert_eq!(c.rows(), 8);
        });
        assert_eq!(loader.builds(), 0, "waited for the other builder instead of building again");
    }

    #[test]
    fn a_stale_build_marker_is_taken_over() {
        let dir = tempfile::tempdir().unwrap();
        let cfg = build_db(dir.path(), &vectors(5, 3), &ids(5), "7");
        let meta = MetaCache::default().index(&cfg).unwrap();
        let key = cache_key(&meta);
        std::fs::create_dir_all(&cfg.cache_dir).unwrap();
        let marker = cfg.cache_dir.join(format!("vectors-{key}.zvec.building"));
        let f = std::fs::File::create(&marker).unwrap();
        f.set_modified(std::time::SystemTime::now() - std::time::Duration::from_secs(3600)).unwrap();
        let loader = Loader::with_fallback(None);
        assert_eq!(loader.get(&cfg, &meta, &|_, _| {}).unwrap().rows(), 5);
        assert_eq!(loader.builds(), 1, "a dead builder's marker does not block forever");
        assert!(!marker.exists(), "the build removes its marker");
    }

    #[test]
    fn a_build_leaves_no_marker_behind() {
        let dir = tempfile::tempdir().unwrap();
        let cfg = build_db(dir.path(), &vectors(4, 4), &ids(4), "7");
        let meta = MetaCache::default().index(&cfg).unwrap();
        let loader = Loader::with_fallback(None);
        loader.get(&cfg, &meta, &|_, _| {}).unwrap();
        assert_eq!(loader.builds(), 1);
        let leftovers: Vec<_> = std::fs::read_dir(&cfg.cache_dir).unwrap().filter_map(Result::ok)
            .filter(|e| e.file_name().to_string_lossy().ends_with(".building")).collect();
        assert!(leftovers.is_empty());
    }
}
