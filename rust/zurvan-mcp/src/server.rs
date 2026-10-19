//! The MCP protocol layer: listings from the captured Python fixtures (so the
//! surface is identical by construction), argument validation, dispatch on a
//! blocking thread, and the error flag of design §7A (A5).

use crate::args::validate;
use crate::config::Config;
use crate::pages;
use crate::status;
use crate::store::MetaCache;
use rmcp::model::{
    CacheScope, CallToolRequestParams, CallToolResponse, CallToolResult, ContentBlock, ErrorData, GetPromptRequestParams,
    GetPromptResponse, GetPromptResult, Implementation, InitializeResult, ListPromptsResult, ListResourceTemplatesResult,
    ListResourcesResult, ListToolsResult, PaginatedRequestParams, Prompt, ReadResourceRequestParams, ReadResourceResponse,
    ReadResourceResult, Resource, ResourceContents, ResourceTemplate, ServerCapabilities, ServerConfig, Tool,
};
use rmcp::service::RequestContext;
use rmcp::{RoleServer, ServerHandler};
use serde_json::{json, Map, Value};
use std::collections::HashMap;
use std::sync::Arc;

pub const INSTRUCTIONS: &str = include_str!("../instructions.md");
const TOOLS_FIXTURE: &str = include_str!("../fixtures/tools_list.json");
const LISTINGS_FIXTURE: &str = include_str!("../fixtures/listings.json");
pub const LIST_TTL_MS: u64 = 300_000;

/// What a tool produced, before it becomes a `CallToolResult`.
#[derive(Debug, Clone, PartialEq)]
pub enum ToolOutput {
    Text(String),
    Stats { nodes: i64, edges: i64 },
    /// An argument-validation failure: structured content absent, as Python.
    ArgError(String),
}

/// A tool result is an error iff its text starts with `Error` or `Failed to`
/// (plan decision). Python returned these with isError false.
pub fn is_error_text(s: &str) -> bool {
    s.starts_with("Error") || s.starts_with("Failed to")
}

struct Surface {
    tools: Vec<Tool>,
    schemas: HashMap<String, Value>,
    resources: Vec<Resource>,
    templates: Vec<ResourceTemplate>,
    prompts: Vec<Prompt>,
    capabilities: ServerCapabilities,
}

fn surface() -> Surface {
    let tools_v: Value = serde_json::from_str(TOOLS_FIXTURE).expect("tools fixture");
    let listings: Value = serde_json::from_str(LISTINGS_FIXTURE).expect("listings fixture");
    let tools: Vec<Tool> = serde_json::from_value(tools_v["tools"].clone()).expect("tools parse");
    let schemas = tools_v["tools"]
        .as_array()
        .unwrap()
        .iter()
        .map(|t| (t["name"].as_str().unwrap().to_string(), t["inputSchema"].clone()))
        .collect();
    Surface {
        tools,
        schemas,
        resources: serde_json::from_value(listings["resources"]["resources"].clone()).expect("resources parse"),
        templates: serde_json::from_value(listings["resourceTemplates"]["resourceTemplates"].clone()).expect("templates parse"),
        prompts: serde_json::from_value(listings["prompts"]["prompts"].clone()).expect("prompts parse"),
        capabilities: serde_json::from_value(listings["initialize"]["capabilities"].clone()).expect("capabilities parse"),
    }
}

#[derive(Clone)]
pub struct ZurvanServer {
    cfg: Arc<Config>,
    surface: Arc<Surface>,
    meta: Arc<MetaCache>,
}

impl ZurvanServer {
    pub fn new(cfg: Config) -> Self {
        ZurvanServer { cfg: Arc::new(cfg), surface: Arc::new(surface()), meta: Arc::new(MetaCache::default()) }
    }

    /// Compute store metadata off the request path, so the first
    /// `zurvan_status` does not pay the one full scan of `chunks`.
    pub fn warm(&self) {
        let me = self.clone();
        std::thread::spawn(move || {
            let _ = me.meta.index(&me.cfg);
            let _ = me.meta.graph(&me.cfg);
        });
    }

    /// Run one already-validated tool call. Blocking: callers run it on a
    /// blocking thread.
    pub fn dispatch(&self, name: &str, args: &Map<String, Value>) -> ToolOutput {
        let s = |k: &str| args.get(k).and_then(Value::as_str).unwrap_or("").to_string();
        let i = |k: &str| args.get(k).and_then(Value::as_i64);
        match name {
            "zurvan_read_page" => ToolOutput::Text(pages::read_page(&self.cfg, &s("path"), i("offset"), i("limit"))),
            "zurvan_status" => ToolOutput::Text(status::status_text(&self.cfg, &self.meta)),
            // Python raises here and the SDK masks an unexpected exception's
            // message: the result is exactly "Error executing tool <name>",
            // isError, no structured content (captured 2026-10-19).
            "zurvan_graph_stats" => match self.meta.graph(&self.cfg) {
                Ok(g) => ToolOutput::Stats { nodes: g.nodes, edges: g.edges },
                Err(_) => ToolOutput::ArgError("Error executing tool zurvan_graph_stats".into()),
            },
            other => ToolOutput::Text(format!("Error: {other} not implemented in this build.")),
        }
    }

    /// `resource_graph_stats` (no thousands separators, as Python).
    pub fn graph_stats_text(&self) -> String {
        match self.meta.graph(&self.cfg) {
            Ok(g) => format!("Graph stats: {} nodes, {} edges", g.nodes, g.edges),
            Err(e) => format!("Error: {e}"),
        }
    }
}

fn to_result(out: ToolOutput) -> CallToolResult {
    match out {
        ToolOutput::Text(t) => {
            let err = is_error_text(&t);
            let mut r = CallToolResult::success(vec![ContentBlock::text(t.clone())]);
            r.structured_content = Some(json!({ "result": t }));
            r.is_error = Some(err);
            r
        }
        ToolOutput::Stats { nodes, edges } => {
            let v = json!({ "nodes": nodes, "edges": edges });
            let text = format!("{{\n  \"nodes\": {nodes},\n  \"edges\": {edges}\n}}");
            let mut r = CallToolResult::success(vec![ContentBlock::text(text)]);
            r.structured_content = Some(v);
            r.is_error = Some(false);
            r
        }
        ToolOutput::ArgError(t) => {
            let mut r = CallToolResult::error(vec![ContentBlock::text(t)]);
            r.structured_content = None;
            r
        }
    }
}

impl ServerHandler for ZurvanServer {
    fn get_info(&self) -> ServerConfig {
        let mut info = Implementation::new("Zurvan", env!("CARGO_PKG_VERSION"));
        info.title = Some("Zurvan knowledge engine".into());
        info.description =
            Some("Local-first knowledge base: hybrid search, a knowledge graph, and durable project memory.".into());
        InitializeResult::new(self.surface.capabilities.clone())
            .with_server_info(info)
            .with_instructions(INSTRUCTIONS)
    }

    async fn list_tools(
        &self,
        _request: Option<PaginatedRequestParams>,
        _context: RequestContext<RoleServer>,
    ) -> Result<ListToolsResult, ErrorData> {
        Ok(ListToolsResult::with_all_items(self.surface.tools.clone())
            .with_ttl_ms(LIST_TTL_MS)
            .with_cache_scope(CacheScope::Private))
    }

    fn get_tool(&self, name: &str) -> Option<Tool> {
        self.surface.tools.iter().find(|t| t.name == name).cloned()
    }

    async fn call_tool(
        &self,
        request: CallToolRequestParams,
        _context: RequestContext<RoleServer>,
    ) -> Result<CallToolResponse, ErrorData> {
        let name = request.name.to_string();
        let Some(schema) = self.surface.schemas.get(&name).cloned() else {
            return Ok(to_result(ToolOutput::ArgError(format!("Unknown tool: {name}"))).into());
        };
        let args = match validate(&name, &schema, request.arguments.as_ref()) {
            Ok(a) => a,
            Err(text) => return Ok(to_result(ToolOutput::ArgError(text)).into()),
        };
        let me = self.clone();
        let out = tokio::task::spawn_blocking(move || me.dispatch(&name, &args))
            .await
            .unwrap_or_else(|e| ToolOutput::Text(format!("Error: tool task failed: {e}")));
        Ok(to_result(out).into())
    }

    async fn list_resources(
        &self,
        _request: Option<PaginatedRequestParams>,
        _context: RequestContext<RoleServer>,
    ) -> Result<ListResourcesResult, ErrorData> {
        Ok(ListResourcesResult::with_all_items(self.surface.resources.clone())
            .with_ttl_ms(LIST_TTL_MS)
            .with_cache_scope(CacheScope::Private))
    }

    async fn list_resource_templates(
        &self,
        _request: Option<PaginatedRequestParams>,
        _context: RequestContext<RoleServer>,
    ) -> Result<ListResourceTemplatesResult, ErrorData> {
        Ok(ListResourceTemplatesResult::with_all_items(self.surface.templates.clone())
            .with_ttl_ms(LIST_TTL_MS)
            .with_cache_scope(CacheScope::Private))
    }

    async fn read_resource(
        &self,
        request: ReadResourceRequestParams,
        _context: RequestContext<RoleServer>,
    ) -> Result<ReadResourceResponse, ErrorData> {
        let uri = request.uri.clone();
        let me = self.clone();
        let text = tokio::task::spawn_blocking(move || pages::resource(&me.cfg, &uri, || me.graph_stats_text()))
            .await
            .map_err(|e| ErrorData::internal_error(e.to_string(), None))?;
        match text {
            Some(t) => Ok(ReadResourceResult::new(vec![ResourceContents::text(t, request.uri)]).into()),
            None => Err(ErrorData::resource_not_found(format!("Unknown resource: {}", request.uri), None)),
        }
    }

    async fn list_prompts(
        &self,
        _request: Option<PaginatedRequestParams>,
        _context: RequestContext<RoleServer>,
    ) -> Result<ListPromptsResult, ErrorData> {
        Ok(ListPromptsResult::with_all_items(self.surface.prompts.clone())
            .with_ttl_ms(LIST_TTL_MS)
            .with_cache_scope(CacheScope::Private))
    }

    async fn get_prompt(
        &self,
        request: GetPromptRequestParams,
        _context: RequestContext<RoleServer>,
    ) -> Result<GetPromptResponse, ErrorData> {
        let Some((description, text)) = pages::prompt(&request.name) else {
            return Err(ErrorData::invalid_params(format!("Unknown prompt: {}", request.name), None));
        };
        let result: GetPromptResult = serde_json::from_value(json!({
            "description": description,
            "messages": [{"role": "user", "content": {"type": "text", "text": text}}]
        }))
        .map_err(|e| ErrorData::internal_error(e.to_string(), None))?;
        Ok(result.into())
    }
}
