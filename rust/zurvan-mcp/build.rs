//! Compiles in the repo root (two levels above this crate) as the default
//! `PROJECT_ROOT`, mirroring `scripts/config.py`'s `Path(__file__).parent.parent`.
use std::path::PathBuf;

fn main() {
    let manifest = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR"));
    let root = manifest.join("..").join("..");
    let root = root.canonicalize().unwrap_or(root);
    println!("cargo:rustc-env=ZURVAN_BUILD_ROOT={}", root.display());
    println!("cargo:rerun-if-changed=build.rs");
}
