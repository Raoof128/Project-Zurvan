//! zurvan-mcp: a drop-in Rust MCP server for the Zurvan knowledge engine.
//!
//! Under the stdio transport, stdout IS the JSON-RPC channel, so nothing in
//! this library may print to it. The lint below enforces that at compile time
//! (the Rust counterpart of `mcp_tools._protocol_safe`).
#![deny(clippy::print_stdout)]

pub mod args;
pub mod bridge;
pub mod client;
pub mod config;
pub mod context;
pub mod corpus;
pub mod doctor;
pub mod embed;
pub mod graph;
pub mod keyword;
pub mod pages;
pub mod search;
pub mod security;
pub mod selftest;
pub mod server;
pub mod status;
pub mod store;
#[cfg(test)]
pub mod testutil;
pub mod text;
pub mod vectors;
