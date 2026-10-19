//! Graph tools: ports of `graph_query.get_neighbours` and
//! `graph_context.expand_graph_context` with the canonical expansion order of
//! design §4.6 (Python's BFS frontier is a `set`, so its order varies with
//! PYTHONHASHSEED; this one does not).

use crate::text::{budget, paginate, py_str, RESULT_CHAR_BUDGET};
use rusqlite::{Connection, OptionalExtension};
use std::collections::HashSet;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Neighbour {
    pub from_title: Option<String>,
    pub edge_type: Option<String>,
    pub to_title: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Expanded {
    pub path: Option<String>,
    pub node_type: Option<String>,
    pub title: Option<String>,
    pub depth: u32,
    pub relation: String,
}

/// `graph_query.get_node` + `get_neighbours`, SQL verbatim.
pub fn neighbours(conn: &Connection, path_or_id: &str) -> rusqlite::Result<Vec<Neighbour>> {
    let node: Option<String> = conn
        .query_row(
            "SELECT node_id FROM nodes WHERE node_id = ? OR path = ? OR slug = ?",
            [path_or_id, path_or_id, path_or_id],
            |r| r.get(0),
        )
        .optional()?;
    let Some(node) = node else { return Ok(Vec::new()) };
    let mut st = conn.prepare(
        "SELECT e.edge_type, e.from_node_id, e.to_node_id,
               n1.path as from_path, n1.title as from_title,
               n2.path as to_path, n2.title as to_title
        FROM edges e
        JOIN nodes n1 ON e.from_node_id = n1.node_id
        JOIN nodes n2 ON e.to_node_id = n2.node_id
        WHERE e.from_node_id = ? OR e.to_node_id = ?",
    )?;
    let rows = st.query_map([&node, &node], |r| {
        Ok(Neighbour { edge_type: r.get(0)?, from_title: r.get(4)?, to_title: r.get(6)? })
    })?;
    rows.collect()
}

pub fn neighbour_line(n: &Neighbour) -> String {
    format!(
        "{} --[{}]--> {}",
        py_str(n.from_title.as_deref()),
        py_str(n.edge_type.as_deref()),
        py_str(n.to_title.as_deref())
    )
}

fn type_rank(t: Option<&str>) -> u8 {
    match t {
        Some("decision") => 0,
        Some("claim") => 1,
        Some("contradiction") => 2,
        Some("open_question") => 3,
        Some("concept") => 4,
        Some("source") => 5,
        _ => 6,
    }
}

/// `expand_graph_context` with a canonical order: seeds resolved in the
/// order given; each BFS frontier processed in `node_id` order; per node,
/// outgoing rows then incoming rows, each by (other node_id, edge_type); a
/// node keeps the depth and relation of its first discovery. Final sort is
/// Python's `(depth, type_rank, title)`, then `path` for ties Python left to
/// set order.
pub fn expand(conn: &Connection, seeds: &[String], depth: u32) -> rusqlite::Result<Vec<Expanded>> {
    let mut seed_ids: Vec<String> = Vec::new();
    for s in seeds {
        let id: Option<String> = conn
            .query_row("SELECT node_id FROM nodes WHERE path = ? OR node_id = ? OR slug = ?", [s, s, s], |r| r.get(0))
            .optional()?;
        if let Some(id) = id {
            if !seed_ids.contains(&id) {
                seed_ids.push(id);
            }
        }
    }
    if seed_ids.is_empty() {
        return Ok(Vec::new());
    }
    let mut visited: HashSet<String> = seed_ids.iter().cloned().collect();
    let mut frontier = seed_ids;
    frontier.sort();
    let mut out = Vec::new();
    let mut outgoing = conn.prepare(
        "SELECT e.edge_type, e.to_node_id, n.path, n.node_type, n.title
         FROM edges e JOIN nodes n ON e.to_node_id = n.node_id
         WHERE e.from_node_id = ? ORDER BY e.to_node_id, e.edge_type",
    )?;
    let mut incoming = conn.prepare(
        "SELECT e.edge_type, e.from_node_id, n.path, n.node_type, n.title
         FROM edges e JOIN nodes n ON e.from_node_id = n.node_id
         WHERE e.to_node_id = ? ORDER BY e.from_node_id, e.edge_type",
    )?;
    type Row = (Option<String>, String, Option<String>, Option<String>, Option<String>);
    let read = |r: &rusqlite::Row| -> rusqlite::Result<Row> { Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)) };
    for d in 1..=depth {
        let mut next: Vec<String> = Vec::new();
        for node_id in &frontier {
            for (stmt, dir) in [(&mut outgoing, "outgoing"), (&mut incoming, "incoming")] {
                let rows: Vec<Row> = stmt.query_map([node_id], read)?.collect::<rusqlite::Result<_>>()?;
                for (edge_type, other, path, node_type, title) in rows {
                    if visited.insert(other.clone()) {
                        next.push(other);
                        out.push(Expanded {
                            path,
                            node_type,
                            title,
                            depth: d,
                            relation: format!("{dir}:{}", py_str(edge_type.as_deref())),
                        });
                    }
                }
            }
        }
        next.sort();
        frontier = next;
    }
    out.sort_by(|a, b| {
        (a.depth, type_rank(a.node_type.as_deref()), &a.title, &a.path)
            .cmp(&(b.depth, type_rank(b.node_type.as_deref()), &b.title, &b.path))
    });
    Ok(out)
}

/// `tool_zurvan_graph_neighbours`. `conn` is Err with the store's error text
/// when the graph cannot be opened.
pub fn neighbours_tool(conn: Result<&Connection, String>, path_or_id: &str, limit: Option<i64>, cursor: Option<i64>) -> String {
    let result = conn.and_then(|c| neighbours(c, path_or_id).map_err(|e| e.to_string()));
    match result {
        Err(e) => format!("Error reading graph neighbours for '{path_or_id}': {e}"),
        Ok(n) if n.is_empty() => format!(
            "No neighbours found for '{path_or_id}'. If the node should exist, check the path with zurvan_search, or rebuild the graph (`zurvan graph rebuild`)."
        ),
        Ok(n) => {
            let (page, header) = paginate(&n, limit, cursor);
            let lines: Vec<String> = page.iter().map(neighbour_line).collect();
            budget(&format!("{header}\n{}", lines.join("\n")), RESULT_CHAR_BUDGET)
        }
    }
}

/// `tool_zurvan_graph_expand`. A missing graph is "no neighbours" (Python's
/// `expand_graph_context` checks `os.path.exists` and returns []).
pub fn expand_tool(conn: Result<&Connection, String>, path_or_id: &str, depth: u32, limit: Option<i64>, cursor: Option<i64>) -> String {
    let not_found = || {
        format!("No graph neighbours found for '{path_or_id}'. Check the path with zurvan_search, or rebuild the graph (`zurvan graph rebuild`).")
    };
    let Ok(conn) = conn else { return not_found() };
    match expand(conn, &[path_or_id.to_string()], depth) {
        Err(e) => format!("Error expanding graph around '{path_or_id}': {e}"),
        Ok(items) if items.is_empty() => not_found(),
        Ok(items) => {
            let (page, header) = paginate(&items, limit, cursor);
            let lines: Vec<String> = page
                .iter()
                .map(|i| format!("[{}] {} ({}) - {}", i.depth, py_str(i.title.as_deref()), py_str(i.node_type.as_deref()), i.relation))
                .collect();
            budget(&format!("{header}\n{}", lines.join("\n")), RESULT_CHAR_BUDGET)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::store::open_ro;
    use crate::testutil::graph_db;

    /// d1 (decision) --links_to--> c1 (claim) --cites--> s1 (source)
    /// s1 --mentions--> d1 (cycle back, so d1 is reachable both ways)
    /// c1 --links_to--> n1 (note, NULL title) ; c2 (claim) --links_to--> c1
    /// x1 --links_to--> c1 and c1 --links_to--> x1 (x1 reached both ways from c1)
    /// t1, t2: two concepts with the SAME title, both linked from c1
    fn fixture() -> (tempfile::TempDir, std::path::PathBuf) {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("graph.sqlite");
        graph_db(
            &p,
            &[
                ("d1", "decision", Some("Decision One"), Some("wiki/decisions/d1.md")),
                ("c1", "claim", Some("Claim One"), Some("wiki/claims/c1.md")),
                ("c2", "claim", Some("Claim Two"), Some("wiki/claims/c2.md")),
                ("s1", "source", Some("Source One"), Some("wiki/sources/s1.md")),
                ("n1", "note", None, Some("wiki/n1.md")),
                ("x1", "concept", Some("Xenon"), Some("wiki/concepts/x1.md")),
                ("t1", "concept", Some("Same"), Some("wiki/concepts/t1.md")),
                ("t2", "concept", Some("Same"), Some("wiki/concepts/t2.md")),
            ],
            &[
                ("d1", "c1", "links_to"), ("c1", "s1", "cites"), ("s1", "d1", "mentions"),
                ("c1", "n1", "links_to"), ("c2", "c1", "links_to"), ("x1", "c1", "links_to"),
                ("c1", "x1", "links_to"), ("c1", "t2", "links_to"), ("c1", "t1", "links_to"),
            ],
        );
        (dir, p)
    }

    #[test]
    fn neighbours_format_and_none() {
        let (_d, p) = fixture();
        let conn = open_ro(&p).unwrap();
        let n = neighbours(&conn, "wiki/claims/c1.md").unwrap();
        assert_eq!(n.len(), 8);
        let lines: Vec<String> = n.iter().map(neighbour_line).collect();
        assert!(lines.contains(&"Claim One --[links_to]--> None".to_string()), "{lines:?}");
        assert!(lines.contains(&"Decision One --[links_to]--> Claim One".to_string()));
        assert!(neighbours(&conn, "nope").unwrap().is_empty());
    }

    #[test]
    fn expand_is_deterministic() {
        let (_d, p) = fixture();
        let conn = open_ro(&p).unwrap();
        let first = expand(&conn, &["wiki/claims/c1.md".to_string()], 2).unwrap();
        for _ in 0..100 {
            assert_eq!(expand(&conn, &["wiki/claims/c1.md".to_string()], 2).unwrap(), first);
        }
    }

    #[test]
    fn expand_depth_is_bfs_level_and_sorted() {
        let (_d, p) = fixture();
        let conn = open_ro(&p).unwrap();
        let items = expand(&conn, &["wiki/decisions/d1.md".to_string()], 2).unwrap();
        let summary: Vec<(u32, Option<&str>, &str)> =
            items.iter().map(|i| (i.depth, i.title.as_deref(), i.relation.as_str())).collect();
        // depth 1 from d1: c1 (outgoing links_to), s1 (incoming mentions). Claim ranks before source.
        assert_eq!(summary[0], (1, Some("Claim One"), "outgoing:links_to"));
        assert_eq!(summary[1], (1, Some("Source One"), "incoming:mentions"));
        // s1 is reachable at depth 2 via c1 --cites--> s1 but stays at depth 1.
        assert_eq!(items.iter().filter(|i| i.path.as_deref() == Some("wiki/sources/s1.md")).count(), 1);
        // depth 2 ordering: claim (c2) < concepts (Same, Same, Xenon) < other (n1, None title).
        let d2: Vec<(Option<&str>, Option<&str>)> =
            items.iter().filter(|i| i.depth == 2).map(|i| (i.title.as_deref(), i.path.as_deref())).collect();
        assert_eq!(d2, vec![
            (Some("Claim Two"), Some("wiki/claims/c2.md")),
            (Some("Same"), Some("wiki/concepts/t1.md")),
            (Some("Same"), Some("wiki/concepts/t2.md")),
            (Some("Xenon"), Some("wiki/concepts/x1.md")),
            (None, Some("wiki/n1.md")),
        ]);
    }

    #[test]
    fn expand_relation_rule() {
        let (_d, p) = fixture();
        let conn = open_ro(&p).unwrap();
        let items = expand(&conn, &["wiki/claims/c1.md".to_string()], 1).unwrap();
        let x1 = items.iter().find(|i| i.path.as_deref() == Some("wiki/concepts/x1.md")).unwrap();
        assert_eq!(x1.relation, "outgoing:links_to", "outgoing rows are processed before incoming rows");
    }

    #[test]
    fn not_found_message_exact() {
        let (_d, p) = fixture();
        let conn = open_ro(&p).unwrap();
        assert_eq!(
            neighbours_tool(Ok(&conn), "zz", None, None),
            "No neighbours found for 'zz'. If the node should exist, check the path with zurvan_search, or rebuild the graph (`zurvan graph rebuild`)."
        );
        assert_eq!(
            expand_tool(Ok(&conn), "zz", 2, None, None),
            "No graph neighbours found for 'zz'. Check the path with zurvan_search, or rebuild the graph (`zurvan graph rebuild`)."
        );
        let page = neighbours_tool(Ok(&conn), "wiki/claims/c1.md", Some(2), Some(0));
        assert!(page.starts_with("Showing 1-2 of 8. More available: call again with cursor=2.\n"), "{page}");
        let ex = expand_tool(Ok(&conn), "wiki/decisions/d1.md", 1, None, None);
        assert_eq!(ex, "Showing 1-2 of 2.\n[1] Claim One (claim) - outgoing:links_to\n[1] Source One (source) - incoming:mentions");
    }
}
