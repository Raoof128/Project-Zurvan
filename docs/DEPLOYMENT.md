# Deployment Guide

Zurvan is built as a **local-first** CLI and MCP server. It is intentionally designed not to be deployed as a traditional web application or cloud service at this time.

## Running Locally as a Daemon
If you wish to keep the MCP server running perpetually in the background for a local client to connect to, you can use standard process managers like `pm2`, `systemd`, or `tmux`. However, because the server operates over `stdio`, it is usually spawned dynamically by the client (e.g., Claude Code, Cursor) and automatically killed when the client exits.

## Network Deployment & HTTP Transport (Future)
Currently, Zurvan does **not** support HTTP transport for the MCP server. Exposing the server over a network without an authentication layer violates the strict security model (preventing arbitrary file reads and unauthorized memory modification).

If you must expose it on an internal network:
1. Wait for Phase 7 (HTTP Transport + Auth) to be completed.
2. If doing it manually now, wrap the `stdio` streams in an authenticated reverse proxy, but do so at your own risk. Always enforce `ZURVAN_MCP_READONLY=1` in such scenarios.
