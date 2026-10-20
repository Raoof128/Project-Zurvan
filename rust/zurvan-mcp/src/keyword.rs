//! Keyword search with exactly the results of `search::keyword` (the port of
//! `context_export._search_internal`), fast (IOU-13).
//!
//! Profiled on the live store 2026-10-20 (11,955 files, 212 MB): the uncached
//! scan spent ~800 ms canonicalising every file for the read gate, ~2.1 s
//! reading and decoding, ~260 ms lowercasing (serial), and only ~24 ms on the
//! substring matching itself; ~580 ms end to end in parallel. Both costs are
//! removed without changing semantics:
//!
//! - **The gate** is decided from the walk itself: the walk already resolves
//!   each directory to its stored canonical path once, so a file's canonical
//!   path is that directory plus its stored leaf name (a symlinked leaf is
//!   canonicalised on its own). Same verdict as `security::open_target`.
//! - **The lowercased text** of every allowed file lives in one memory-mapped
//!   cache file (`data/cache/keyword-<key>.zkw`, 0600 in a 0700 dir; the temp
//!   fallback when the store is not writable). An entry is used only while its
//!   file's identity (dev, inode, size, mtime) is unchanged; a changed or new
//!   file is read fresh and kept in a small overlay, so an edit is visible to
//!   the very next query. After `REBUILD_AFTER` such misses the file is
//!   rebuilt. Every query re-walks and re-gates, so the cache can never serve
//!   a file the gate now refuses.

use crate::corpus::{glob_md_entries, rel};
use crate::search::{Hit, Score};
use crate::text::read_universal;
use memmap2::Mmap;
use rayon::prelude::*;
use std::collections::HashMap;
use std::io::{BufWriter, Seek, SeekFrom, Write};
use std::os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, RwLock};

const MAGIC: &[u8; 8] = b"ZKWCACH1";
/// magic, entry count, index offset, index length.
const HEADER: usize = 32;
/// Changed or new files served from the overlay before the cache is rebuilt.
const REBUILD_AFTER: usize = 256;
static NONCE: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Ident {
    dev: u64,
    ino: u64,
    size: u64,
    mtime_ns: i64,
}

impl Ident {
    fn of(m: &std::fs::Metadata) -> Ident {
        Ident { dev: m.dev(), ino: m.ino(), size: m.size(), mtime_ns: m.mtime() * 1_000_000_000 + m.mtime_nsec() }
    }
}

struct Entry {
    ident: Ident,
    /// Byte range of the lowercased text; None = the file is not text
    /// (`read_universal` failed), which the scan skips.
    text: Option<(usize, usize)>,
}

struct Blob {
    root: PathBuf,
    map: Mmap,
    entries: HashMap<PathBuf, Entry>,
}

impl Blob {
    fn open(path: &Path, root: &Path) -> Result<Blob, String> {
        let file = std::fs::File::open(path).map_err(|e| e.to_string())?;
        // SAFETY: the file is only ever replaced by rename, never written in
        // place, so the mapped inode cannot change under us.
        let map = unsafe { Mmap::map(&file) }.map_err(|e| e.to_string())?;
        let u64_at = |o: usize| map.get(o..o + 8).map(|b| u64::from_le_bytes(b.try_into().unwrap()));
        if map.len() < HEADER || &map[..8] != MAGIC {
            return Err("not a keyword cache".into());
        }
        let (count, ioff, ilen) = (u64_at(8).unwrap() as usize, u64_at(16).unwrap() as usize, u64_at(24).unwrap() as usize);
        let index = map.get(ioff..ioff.checked_add(ilen).ok_or("bad index")?).ok_or("index out of range")?;
        let mut entries = HashMap::with_capacity(count);
        let mut at = 0usize;
        let take = |at: &mut usize, n: usize| -> Result<&[u8], String> {
            let s = index.get(*at..*at + n).ok_or("truncated index")?;
            *at += n;
            Ok(s)
        };
        let rd = |b: &[u8]| u64::from_le_bytes(b.try_into().unwrap());
        root_check(take(&mut at, 8).map(rd)? as usize, &mut at, index, root)?;
        for _ in 0..count {
            let n = u32::from_le_bytes(take(&mut at, 4)?.try_into().unwrap()) as usize;
            let p = PathBuf::from(String::from_utf8(take(&mut at, n)?.to_vec()).map_err(|e| e.to_string())?);
            let ident = Ident {
                dev: rd(take(&mut at, 8)?),
                ino: rd(take(&mut at, 8)?),
                size: rd(take(&mut at, 8)?),
                mtime_ns: rd(take(&mut at, 8)?) as i64,
            };
            let readable = take(&mut at, 1)?[0] == 1;
            let (off, len) = (rd(take(&mut at, 8)?) as usize, rd(take(&mut at, 8)?) as usize);
            let text = if readable {
                let end = off.checked_add(len).ok_or("bad range")?;
                if off < HEADER || end > ioff {
                    return Err("text out of range".into());
                }
                Some((off, len))
            } else {
                None
            };
            entries.insert(p, Entry { ident, text });
        }
        // Validated once here, so lookups can hand out &str without checks.
        let ok = entries.par_iter().all(|(_, e)| e.text.is_none_or(|(o, l)| std::str::from_utf8(&map[o..o + l]).is_ok()));
        if !ok {
            return Err("invalid text in keyword cache".into());
        }
        Ok(Blob { root: root.to_path_buf(), map, entries })
    }

    /// Some(Some(text)) cached text; Some(None) cached as not-text; None = no
    /// entry for this exact file identity.
    fn get(&self, p: &Path, id: Ident) -> Option<Option<&str>> {
        let e = self.entries.get(p).filter(|e| e.ident == id)?;
        // SAFETY: validated as UTF-8 in `open`.
        Some(e.text.map(|(o, l)| unsafe { std::str::from_utf8_unchecked(&self.map[o..o + l]) }))
    }
}

/// The index starts with the canonical root it was built for.
fn root_check(n: usize, at: &mut usize, index: &[u8], root: &Path) -> Result<(), String> {
    let s = index.get(*at..*at + n).ok_or("truncated root")?;
    *at += n;
    (s == root.as_os_str().as_encoded_bytes()).then_some(()).ok_or_else(|| "built for another root".to_string())
}

/// Lowercased text for one file, from wherever it is held.
enum Src<'a> {
    Text(&'a str),
    Owned(Arc<str>),
    Skip,
}

impl Src<'_> {
    fn text(&self) -> Option<&str> {
        match self {
            Src::Text(t) => Some(t),
            Src::Owned(t) => Some(t),
            Src::Skip => None,
        }
    }
}

/// Changed or new files since the cache file was built: identity and
/// lowercased text (None = not text).
type Overlay = HashMap<PathBuf, (Ident, Option<Arc<str>>)>;

pub struct KeywordCache {
    /// Where the cache file may live, in order (the store's data/cache, then
    /// the private temp fallback). Empty = never persist (uncached scan).
    dirs: Vec<PathBuf>,
    blob: RwLock<Option<Arc<Blob>>>,
    overlay: RwLock<Overlay>,
    rebuilding: Mutex<()>,
    builds: AtomicUsize,
}

impl KeywordCache {
    pub fn new(dirs: Vec<PathBuf>) -> KeywordCache {
        KeywordCache {
            dirs,
            blob: RwLock::new(None),
            overlay: RwLock::new(HashMap::new()),
            rebuilding: Mutex::new(()),
            builds: AtomicUsize::new(0),
        }
    }

    /// Cache files this instance wrote (tests; doctor).
    pub fn builds(&self) -> usize {
        self.builds.load(Ordering::SeqCst)
    }

    fn file_name(root: &Path) -> String {
        use sha2::{Digest, Sha256};
        let hex: String = Sha256::digest(root.as_os_str().as_encoded_bytes()).iter().take(8).map(|b| format!("{b:02x}")).collect();
        format!("keyword-{hex}.zkw")
    }

    fn current(&self, root: &Path) -> Option<Arc<Blob>> {
        if let Some(b) = self.blob.read().unwrap().as_ref().filter(|b| b.root == root) {
            return Some(b.clone());
        }
        let name = Self::file_name(root);
        let b = self.dirs.iter().find_map(|d| Blob::open(&d.join(&name), root).ok()).map(Arc::new)?;
        *self.blob.write().unwrap() = Some(b.clone());
        Some(b)
    }

    /// `search::keyword(root, query, limit)`, the same hits in the same order.
    pub fn search(&self, root: &Path, query: &str, limit: usize) -> Vec<Hit> {
        let Ok(root_c) = root.canonicalize() else { return crate::search::keyword(root, query, limit) };
        if self.dirs.is_empty() {
            return crate::search::keyword(root, query, limit);
        }
        let tokens: Vec<String> =
            query.to_lowercase().split(crate::text::py_isspace).filter(|t| !t.is_empty()).map(String::from).collect();
        let files = walk(root, &root_c);
        let ids: Vec<Option<Ident>> = files
            .par_iter()
            .map(|(_, c)| std::fs::metadata(c).ok().filter(|m| m.is_file()).map(|m| Ident::of(&m)))
            .collect();
        let mut blob = self.current(&root_c);
        let misses: Vec<usize> = {
            let overlay = self.overlay.read().unwrap();
            (0..files.len())
                .filter(|&i| {
                    ids[i].is_some_and(|id| {
                        let c = &files[i].1;
                        blob.as_ref().and_then(|b| b.get(c, id)).is_none() && overlay.get(c).is_none_or(|(oid, _)| *oid != id)
                    })
                })
                .collect()
        };
        if blob.is_none() || misses.len() > REBUILD_AFTER {
            match self.rebuild(&root_c, &files, &ids, blob.as_deref()) {
                Ok(b) => blob = Some(b),
                Err(_) => return crate::search::keyword(root, query, limit),
            }
        } else if !misses.is_empty() {
            let fresh: Vec<(PathBuf, Ident, Option<Arc<str>>)> = misses
                .par_iter()
                .map(|&i| {
                    let (c, id) = (&files[i].1, ids[i].unwrap());
                    (c.clone(), id, read_universal(c).ok().map(|s| Arc::from(s.to_lowercase())))
                })
                .collect();
            let mut overlay = self.overlay.write().unwrap();
            for (c, id, text) in fresh {
                overlay.insert(c, (id, text));
            }
        }
        let blob = blob.expect("built above");
        let overlay = self.overlay.read().unwrap();
        let mut scored: Vec<(usize, i64)> = (0..files.len())
            .into_par_iter()
            .filter_map(|i| {
                let id = ids[i]?;
                let c = &files[i].1;
                let src = match blob.get(c, id) {
                    Some(Some(t)) => Src::Text(t),
                    Some(None) => Src::Skip,
                    None => match overlay.get(c).filter(|(oid, _)| *oid == id) {
                        Some((_, Some(t))) => Src::Owned(t.clone()),
                        _ => Src::Skip,
                    },
                };
                let lower = src.text()?;
                let score = tokens.iter().filter(|t| lower.contains(t.as_str())).count() as i64;
                (score > 0).then_some((i, score))
            })
            .collect();
        drop(overlay);
        // Stable: ties keep walk order, as Python's sort does.
        scored.sort_by_key(|&(_, s)| std::cmp::Reverse(s));
        scored
            .into_iter()
            .filter_map(|(i, score)| {
                let text = read_universal(&files[i].1).ok()?;
                Some(Hit {
                    chunk_id: String::new(),
                    source_path: files[i].0.clone(),
                    heading: None,
                    text,
                    keyword: 0.0,
                    semantic: 0.0,
                    score: Score::I(score),
                })
            })
            .take(limit)
            .collect()
    }

    /// Write a new cache file for the current walk (reusing unchanged text
    /// from the old one and the overlay), atomically, and switch to it.
    fn rebuild(&self, root: &Path, files: &[(String, PathBuf)], ids: &[Option<Ident>], old: Option<&Blob>) -> Result<Arc<Blob>, String> {
        let _one = self.rebuilding.lock().unwrap();
        let name = Self::file_name(root);
        let mut last_err = "no cache directory".to_string();
        for dir in &self.dirs {
            if let Err(e) = prepare_dir(dir, dir == &self.dirs[0]) {
                last_err = e;
                continue;
            }
            match self.write_blob(&dir.join(&name), root, files, ids, old) {
                Ok(()) => {
                    let b = Arc::new(Blob::open(&dir.join(&name), root)?);
                    self.builds.fetch_add(1, Ordering::SeqCst);
                    *self.blob.write().unwrap() = Some(b.clone());
                    self.overlay.write().unwrap().clear();
                    return Ok(b);
                }
                Err(e) => last_err = e,
            }
        }
        Err(last_err)
    }

    fn write_blob(&self, dest: &Path, root: &Path, files: &[(String, PathBuf)], ids: &[Option<Ident>], old: Option<&Blob>) -> Result<(), String> {
        let tmp = dest.with_extension(format!("zkw.tmp-{}-{}", std::process::id(), NONCE.fetch_add(1, Ordering::SeqCst)));
        let result = (|| -> Result<(), String> {
            let file = std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(&tmp).map_err(|e| e.to_string())?;
            let mut w = BufWriter::with_capacity(8 << 20, file);
            w.write_all(&[0u8; HEADER]).map_err(|e| e.to_string())?;
            let mut pos = HEADER;
            let mut index: Vec<u8> = Vec::with_capacity(files.len() * 96);
            let root_bytes = root.as_os_str().as_encoded_bytes();
            index.extend_from_slice(&(root_bytes.len() as u64).to_le_bytes());
            index.extend_from_slice(root_bytes);
            let overlay = self.overlay.read().unwrap();
            let mut count = 0u64;
            let order: Vec<usize> = (0..files.len()).filter(|&i| ids[i].is_some()).collect();
            for chunk in order.chunks(512) {
                let texts: Vec<(usize, Option<std::borrow::Cow<str>>)> = chunk
                    .par_iter()
                    .map(|&i| {
                        let (c, id) = (&files[i].1, ids[i].unwrap());
                        let t = match old.and_then(|b| b.get(c, id)) {
                            Some(t) => t.map(std::borrow::Cow::Borrowed),
                            None => match overlay.get(c).filter(|(oid, _)| *oid == id) {
                                Some((_, t)) => t.as_ref().map(|s| std::borrow::Cow::Owned(s.to_string())),
                                None => read_universal(c).ok().map(|s| std::borrow::Cow::Owned(s.to_lowercase())),
                            },
                        };
                        (i, t)
                    })
                    .collect();
                for (i, t) in texts {
                    let (c, id) = (&files[i].1, ids[i].unwrap());
                    let p = c.as_os_str().as_encoded_bytes();
                    index.extend_from_slice(&(p.len() as u32).to_le_bytes());
                    index.extend_from_slice(p);
                    for v in [id.dev, id.ino, id.size, id.mtime_ns as u64] {
                        index.extend_from_slice(&v.to_le_bytes());
                    }
                    match t {
                        Some(text) => {
                            index.push(1);
                            index.extend_from_slice(&(pos as u64).to_le_bytes());
                            index.extend_from_slice(&(text.len() as u64).to_le_bytes());
                            w.write_all(text.as_bytes()).map_err(|e| e.to_string())?;
                            pos += text.len();
                        }
                        None => {
                            index.push(0);
                            index.extend_from_slice(&[0u8; 16]);
                        }
                    }
                    count += 1;
                }
            }
            drop(overlay);
            w.write_all(&index).map_err(|e| e.to_string())?;
            let mut header = [0u8; HEADER];
            header[..8].copy_from_slice(MAGIC);
            header[8..16].copy_from_slice(&count.to_le_bytes());
            header[16..24].copy_from_slice(&(pos as u64).to_le_bytes());
            header[24..32].copy_from_slice(&(index.len() as u64).to_le_bytes());
            w.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
            w.write_all(&header).map_err(|e| e.to_string())?;
            let f = w.into_inner().map_err(|e| e.to_string())?;
            f.sync_all().map_err(|e| e.to_string())?;
            std::fs::rename(&tmp, dest).map_err(|e| e.to_string())
        })();
        if result.is_err() {
            let _ = std::fs::remove_file(&tmp);
        }
        result
    }
}

/// The store's cache dir is created 0700 and tightened; the fallback must be
/// a private directory this account owns (`vectors::private_dir`).
fn prepare_dir(dir: &Path, primary: bool) -> Result<(), String> {
    if primary {
        std::fs::DirBuilder::new().recursive(true).mode(0o700).create(dir).map_err(|e| e.to_string())?;
        std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700)).map_err(|e| e.to_string())
    } else {
        crate::vectors::private_dir(dir)
    }
}

/// The keyword corpus in walk order: (requested repo-relative path, canonical
/// path) for every `*.md` under wiki/ and docs/ that has no `traces` segment
/// and whose canonical path passes the read gate. Equivalent to
/// `security::open_target` per file, at one lstat-free decision per entry.
fn walk(root: &Path, root_c: &Path) -> Vec<(String, PathBuf)> {
    ["wiki", "docs"]
        .iter()
        .flat_map(|d| glob_md_entries(&root.join(d)))
        .filter_map(|e| {
            let r = rel(root, &e.path);
            if r.split('/').any(|seg| seg == "traces") {
                return None;
            }
            let canonical = if e.is_symlink { std::fs::canonicalize(&e.path).ok()? } else { e.dir_canonical.join(&e.name) };
            crate::security::allowed_canonical(root_c, &canonical, false).then_some((r, canonical))
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn store() -> (tempfile::TempDir, tempfile::TempDir, PathBuf) {
        let outside = tempfile::tempdir().unwrap();
        fs::write(outside.path().join("out.md"), "alpha outside secret").unwrap();
        let dir = tempfile::tempdir().unwrap();
        let r = dir.path().to_path_buf();
        for d in ["wiki/traces", "wiki/sub", "wiki/dir.md", "docs/evaluation/retrieval", "docs/x"] {
            fs::create_dir_all(r.join(d)).unwrap();
        }
        fs::write(r.join("wiki/a.md"), "Alpha beta").unwrap();
        fs::write(r.join("wiki/b.md"), "ALPHA alpha gamma").unwrap();
        fs::write(r.join("wiki/sub/c.md"), "beta\r\nalpha\rdelta").unwrap();
        fs::write(r.join("wiki/istanbul.md"), "\u{130}STANBUL STRASSE stra\u{df}e").unwrap();
        fs::write(r.join("wiki/bin.md"), [0x61u8, 0x6c, 0xff, 0xfe, 0x00]).unwrap();
        fs::write(r.join("wiki/traces/t.md"), "alpha trace").unwrap();
        fs::write(r.join("docs/evaluation/retrieval/r.md"), "alpha report").unwrap();
        fs::write(r.join("docs/x/d.md"), "gamma delta alpha").unwrap();
        std::os::unix::fs::symlink(outside.path().join("out.md"), r.join("wiki/escape.md")).unwrap();
        std::os::unix::fs::symlink(r.join("wiki/traces/t.md"), r.join("wiki/to-trace.md")).unwrap();
        std::os::unix::fs::symlink(r.join("docs/x"), r.join("wiki/linked")).unwrap();
        (dir, outside, r)
    }

    const QUERIES: [&str; 7] = ["alpha", "ALPHA beta", "stra\u{df}e", "i\u{307}stanbul", "missing", "alpha alpha", "delta"];

    fn assert_same(cache: &KeywordCache, root: &Path) {
        for q in QUERIES {
            assert_eq!(cache.search(root, q, 10), crate::search::keyword(root, q, 10), "query {q:?}");
        }
    }

    /// The cache is an accelerator, never a change of results: every query
    /// returns exactly what the uncached scan returns, cold and warm.
    #[test]
    fn cached_keyword_matches_the_uncached_scan() {
        let (_d, _o, root) = store();
        let cache = KeywordCache::new(vec![root.join("data/cache")]);
        assert_same(&cache, &root);
        assert_eq!(cache.builds(), 1, "the first query built the cache");
        assert_same(&cache, &root);
        assert_eq!(cache.builds(), 1, "and later queries reuse it");
        assert!(!cache.search(&root, "secret", 10).iter().any(|h| h.text.contains("secret")));
    }

    #[test]
    fn cached_keyword_sees_edits_new_and_deleted_files_at_once() {
        let (_d, _o, root) = store();
        let cache = KeywordCache::new(vec![root.join("data/cache")]);
        assert_same(&cache, &root);
        fs::write(root.join("wiki/a.md"), "omega now, no first letter word").unwrap();
        fs::write(root.join("wiki/new.md"), "alpha alpha brand new").unwrap();
        fs::remove_file(root.join("wiki/b.md")).unwrap();
        assert_same(&cache, &root);
        assert_eq!(cache.builds(), 1, "a few changes are served without a rebuild");
    }

    /// Every query re-walks and re-gates: a cached file that moves into
    /// `traces/` behind a symlink (same inode, same size, same mtime) is
    /// refused, not served from the cache.
    #[test]
    fn cached_keyword_reapplies_the_gate_after_a_symlink_swap() {
        let (_d, _o, root) = store();
        fs::write(root.join("wiki/s.md"), "zebracorn private").unwrap();
        let cache = KeywordCache::new(vec![root.join("data/cache")]);
        assert_eq!(cache.search(&root, "zebracorn", 10).len(), 1);
        fs::rename(root.join("wiki/s.md"), root.join("wiki/traces/s.md")).unwrap();
        std::os::unix::fs::symlink(root.join("wiki/traces/s.md"), root.join("wiki/s.md")).unwrap();
        assert!(cache.search(&root, "zebracorn", 10).is_empty());
        assert!(crate::search::keyword(&root, "zebracorn", 10).is_empty());
    }

    #[test]
    fn the_keyword_cache_is_private_and_reused_by_a_fresh_process() {
        let (_d, _o, root) = store();
        let dir = root.join("data/cache");
        KeywordCache::new(vec![dir.clone()]).search(&root, "alpha", 10);
        let file = fs::read_dir(&dir).unwrap().filter_map(Result::ok)
            .find(|e| e.file_name().to_string_lossy().ends_with(".zkw")).expect("cache file").path();
        assert_eq!(fs::metadata(&file).unwrap().permissions().mode() & 0o777, 0o600);
        assert_eq!(fs::metadata(&dir).unwrap().permissions().mode() & 0o777, 0o700);
        let fresh = KeywordCache::new(vec![dir.clone()]);
        assert_same(&fresh, &root);
        assert_eq!(fresh.builds(), 0, "a fresh process opens the existing cache");
    }

    #[test]
    fn a_corrupt_cache_file_is_rebuilt_not_trusted() {
        let (_d, _o, root) = store();
        let dir = root.join("data/cache");
        KeywordCache::new(vec![dir.clone()]).search(&root, "alpha", 10);
        let file = fs::read_dir(&dir).unwrap().filter_map(Result::ok)
            .find(|e| e.file_name().to_string_lossy().ends_with(".zkw")).unwrap().path();
        let mut bytes = fs::read(&file).unwrap();
        let n = bytes.len();
        bytes[n - 3] ^= 0xff;
        bytes[40] = 0xff;
        fs::write(&file, bytes).unwrap();
        let fresh = KeywordCache::new(vec![dir]);
        assert_same(&fresh, &root);
    }

    #[test]
    fn many_changes_trigger_one_rebuild() {
        let (_d, _o, root) = store();
        let cache = KeywordCache::new(vec![root.join("data/cache")]);
        cache.search(&root, "alpha", 10);
        for i in 0..(REBUILD_AFTER + 5) {
            fs::write(root.join(format!("wiki/bulk{i}.md")), format!("alpha bulk {i}")).unwrap();
        }
        assert_same(&cache, &root);
        assert_eq!(cache.builds(), 2, "a bulk change rebuilds once instead of growing the overlay");
    }

    #[test]
    fn without_a_writable_place_it_is_the_uncached_scan() {
        let (_d, _o, root) = store();
        assert_same(&KeywordCache::new(vec![]), &root);
    }
}
