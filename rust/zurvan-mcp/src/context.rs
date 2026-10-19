//! `zurvan_context`: port of `context_export.export_context(topic, limit,
//! hybrid, graph, depth=1)` in its Markdown form.

use crate::graph::{expand, Expanded};
use crate::search::{Hit, Score, SearchState};
use crate::store::open_graph;
use crate::text::{py_str, take_chars};
use std::collections::HashMap;

/// `_dedupe_sources(matches, max_per_source)`: keep ranking order, drop a
/// source's chunks beyond the cap (R4b).
fn dedupe_sources(matches: Vec<Hit>, max_per_source: usize) -> Vec<Hit> {
    let mut counts: HashMap<String, usize> = HashMap::new();
    matches
        .into_iter()
        .filter(|m| {
            let n = counts.entry(m.source_path.clone()).or_insert(0);
            *n += 1;
            *n <= max_per_source
        })
        .collect()
}

fn node_line(n: &Expanded) -> String {
    format!(
        "- **{}** (`{}`) [Depth: {}] ({})",
        py_str(n.title.as_deref()),
        py_str(n.path.as_deref()),
        n.depth,
        n.relation
    )
}

/// The bundle text (unbudgeted, unstamped) and the semantic-fallback notice,
/// if any. The caller budgets it to 24,000 characters as
/// `tool_zurvan_context` does, then prepends the stamp.
pub fn bundle(st: &SearchState, topic: &str, hybrid: bool, graph: bool, limit: usize) -> Result<(String, Option<String>), String> {
    let (candidates, notice) = st.search(topic, hybrid, limit * 3, &|_, _| {})?;
    let mut results = dedupe_sources(candidates, 2);
    results.truncate(limit);

    let mut out: Vec<String> = vec![format!("# Zurvan Context Bundle: {topic}\n"), "## Search Matches\n".into()];
    let mut seeds: Vec<String> = Vec::new();
    if results.is_empty() {
        out.push("No matching context found.".into());
    } else {
        for r in &results {
            let score = match r.score {
                Score::F(f) => f,
                Score::I(i) => i as f64,
            };
            out.push(format!("### Source: {} (Score: {score:.2})", r.source_path));
            out.push("```markdown".into());
            let truncated = r.text.chars().count() > 1000;
            out.push(format!("{}{}", take_chars(&r.text, 1000), if truncated { "\n...[truncated]..." } else { "" }));
            out.push("```\n".into());
            seeds.push(r.source_path.clone());
        }
    }

    if graph && !seeds.is_empty() {
        // expand_graph_context returns [] when the graph store is missing.
        let nodes = match open_graph(&st.cfg) {
            Ok(conn) => expand(&conn, &seeds, 1).map_err(|e| e.to_string())?,
            Err(_) => Vec::new(),
        };
        if !nodes.is_empty() {
            out.push("## Graph-Related Context\n".into());
            let groups = [
                ("Related Decisions", "decision"),
                ("Related Claims", "claim"),
                ("Related Concepts", "concept"),
                ("Related Sources", "source"),
                ("Contradictions", "contradiction"),
                ("Open Questions", "open_question"),
            ];
            for (title, ty) in groups {
                let members: Vec<&Expanded> = nodes.iter().filter(|n| n.node_type.as_deref() == Some(ty)).collect();
                if !members.is_empty() {
                    out.push(format!("### {title}\n"));
                    out.extend(members.iter().map(|n| node_line(n)));
                    out.push(String::new());
                }
            }
            let rendered: Vec<&str> = groups.iter().map(|g| g.1).collect();
            let other: Vec<&Expanded> =
                nodes.iter().filter(|n| !n.node_type.as_deref().is_some_and(|t| rendered.contains(&t))).collect();
            if !other.is_empty() {
                out.push("### Other Related Notes\n".into());
                out.extend(other.iter().map(|n| node_line(n)));
                out.push(String::new());
            }
        }
    }
    Ok((out.join("\n"), notice))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;
    use crate::embed::mock;
    use crate::search::SearchState;
    use crate::testutil::{graph_db, search_db, Chunk};

    fn store(chunks: &[(&str, &str, &str)]) -> (tempfile::TempDir, SearchState) {
        let dir = tempfile::tempdir().unwrap();
        let cfg = Config::resolve_from(Some(dir.path().to_path_buf()), |_| None);
        let cs: Vec<Chunk> = chunks
            .iter()
            .map(|(id, p, t)| Chunk { chunk_id: id, source_path: p, heading: None, text: t, indexed_at: "2026-10-18T00:00:00", vector: mock(t) })
            .collect();
        search_db(&cfg.search_db, "mock", "deterministic_hash", &cs, &[("built_from_revision", "1")]);
        (dir, SearchState::new(cfg))
    }

    #[test]
    fn no_matches_text() {
        let dir = tempfile::tempdir().unwrap();
        let st = SearchState::new(Config::resolve_from(Some(dir.path().to_path_buf()), |_| None));
        let (text, notice) = bundle(&st, "anything", true, true, 10).unwrap();
        assert_eq!(text, "# Zurvan Context Bundle: anything\n\n## Search Matches\n\nNo matching context found.");
        assert!(notice.is_none());
    }

    #[test]
    fn dedupe_caps_two_per_source() {
        let (_d, st) = store(&[
            ("a1", "wiki/a.md", "ledger revision one"), ("a2", "wiki/a.md", "ledger revision two"),
            ("a3", "wiki/a.md", "ledger revision three"), ("b1", "wiki/b.md", "ledger revision four"),
        ]);
        let (text, _) = bundle(&st, "ledger revision", true, false, 10).unwrap();
        assert_eq!(text.matches("### Source: wiki/a.md").count(), 2);
        assert_eq!(text.matches("### Source: wiki/b.md").count(), 1);
        assert!(text.contains("```markdown\nledger revision"));
    }

    #[test]
    fn truncation_marker_at_1000_chars() {
        let long = "é".repeat(1500);
        let (_d, st) = store(&[("x", "wiki/x.md", &long)]);
        let (text, _) = bundle(&st, &long, true, false, 10).unwrap();
        assert!(text.contains(&format!("```markdown\n{}\n...[truncated]...\n```\n", "é".repeat(1000))), "{}", &text[..200]);
        let short = "é".repeat(1000);
        let (_d2, st2) = store(&[("y", "wiki/y.md", &short)]);
        let (t2, _) = bundle(&st2, &short, true, false, 10).unwrap();
        assert!(t2.contains(&format!("```markdown\n{short}\n```\n")), "exactly 1000 characters is not truncated");
    }

    #[test]
    fn keyword_scores_print_two_decimals() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("wiki")).unwrap();
        std::fs::write(dir.path().join("wiki/k.md"), "alpha beta").unwrap();
        let st = SearchState::new(Config::resolve_from(Some(dir.path().to_path_buf()), |_| None));
        let (text, _) = bundle(&st, "alpha beta", false, false, 10).unwrap();
        assert!(text.contains("### Source: wiki/k.md (Score: 2.00)"), "{text}");
    }

    #[test]
    fn graph_groups_in_python_order() {
        let (_d, st) = store(&[("s", "wiki/seed.md", "the seed page")]);
        graph_db(
            &st.cfg.graph_db,
            &[
                ("seed", "note", Some("Seed"), Some("wiki/seed.md")),
                ("d", "decision", Some("D"), Some("wiki/d.md")),
                ("c", "claim", Some("C"), Some("wiki/c.md")),
                ("k", "concept", Some("K"), Some("wiki/k.md")),
                ("s1", "source", Some("S"), Some("wiki/s.md")),
                ("x", "contradiction", Some("X"), Some("wiki/x.md")),
                ("q", "open_question", Some("Q"), Some("wiki/q.md")),
                ("n", "note", None, None),
            ],
            &[("seed", "d", "links_to"), ("c", "seed", "cites"), ("seed", "k", "links_to"), ("seed", "s1", "links_to"),
              ("seed", "x", "links_to"), ("seed", "q", "links_to"), ("seed", "n", "links_to")],
        );
        let (text, _) = bundle(&st, "the seed page", true, true, 10).unwrap();
        let graph = text.split("## Graph-Related Context\n").nth(1).expect("graph section");
        assert_eq!(
            graph,
            "\n### Related Decisions\n\n- **D** (`wiki/d.md`) [Depth: 1] (outgoing:links_to)\n\n\
             ### Related Claims\n\n- **C** (`wiki/c.md`) [Depth: 1] (incoming:cites)\n\n\
             ### Related Concepts\n\n- **K** (`wiki/k.md`) [Depth: 1] (outgoing:links_to)\n\n\
             ### Related Sources\n\n- **S** (`wiki/s.md`) [Depth: 1] (outgoing:links_to)\n\n\
             ### Contradictions\n\n- **X** (`wiki/x.md`) [Depth: 1] (outgoing:links_to)\n\n\
             ### Open Questions\n\n- **Q** (`wiki/q.md`) [Depth: 1] (outgoing:links_to)\n\n\
             ### Other Related Notes\n\n- **None** (`None`) [Depth: 1] (outgoing:links_to)\n"
        );
    }
}
