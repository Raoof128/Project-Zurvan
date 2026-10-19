//! zurvan-mcp: a drop-in Rust MCP server for the Zurvan knowledge engine.
//!
//! Under the stdio transport, stdout IS the JSON-RPC channel, so nothing in
//! this library may print to it. The lint below enforces that at compile time
//! (the Rust counterpart of `mcp_tools._protocol_safe`).
#![deny(clippy::print_stdout)]

pub mod config;
pub mod text;
