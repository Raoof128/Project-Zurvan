//! `zurvan_context`: port of `context_export.export_context(topic, limit,
//! hybrid, graph, depth=1)` in its Markdown form.

use crate::graph::{expand, Expanded};
use crate::search::{Hit, Score, SearchState, Served};
use crate::store::{open_graph_at, Generation, GENERATION_RETRIES, GRAPH_GENERATION_CHANGED};
use crate::text::{py_str, take_chars};
use std::collections::HashMap;

/// A context bundle bound to what served it (F6): the index generation the
/// matches came from and the graph generation that was expanded, if any.
pub struct Bundle {
    pub text: String,
    pub notice: Option<String>,
    pub served: Served,
    pub graph: Option<Generation>,
}

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
pub fn bundle(
    st: &SearchState,
    topic: &str,
    hybrid: bool,
    graph: bool,
    limit: usize,
    on_progress: &dyn Fn(u64, u64),
) -> Result<Bundle, String> {
    let r = st.search(topic, hybrid, limit * 3, on_progress)?;
    let (candidates, notice, served) = (r.hits, r.notice, r.served);
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

    let mut graph_generation = None;
    if graph && !seeds.is_empty() {
        // expand_graph_context returns [] when the graph store is missing.
        // The open is generation-pinned; a promote landing on it is retried,
        // then reported, never silently served under the old stamp (F6).
        let mut opened = open_graph_at(&st.cfg);
        for _ in 0..GENERATION_RETRIES {
            match &opened {
                Err(e) if e == GRAPH_GENERATION_CHANGED => opened = open_graph_at(&st.cfg),
                _ => break,
            }
        }
        let nodes = match opened {
            Ok((conn, g)) => {
                graph_generation = Some(g);
                expand(&conn, &seeds, 1).map_err(|e| e.to_string())?
            }
            Err(e) if e == GRAPH_GENERATION_CHANGED => return Err(e),
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
    Ok(Bundle { text: out.join("\n"), notice, served, graph: graph_generation })
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
        let b = bundle(&st, "anything", true, true, 10, &|_, _| {}).unwrap();
        let (text, notice) = (b.text, b.notice);
        assert_eq!(text, "# Zurvan Context Bundle: anything\n\n## Search Matches\n\nNo matching context found.");
        assert!(notice.is_none());
    }

    #[test]
    fn dedupe_caps_two_per_source() {
        let (_d, st) = store(&[
            ("a1", "wiki/a.md", "ledger revision one"), ("a2", "wiki/a.md", "ledger revision two"),
            ("a3", "wiki/a.md", "ledger revision three"), ("b1", "wiki/b.md", "ledger revision four"),
        ]);
        let text = bundle(&st, "ledger revision", true, false, 10, &|_, _| {}).unwrap().text;
        assert_eq!(text.matches("### Source: wiki/a.md").count(), 2);
        assert_eq!(text.matches("### Source: wiki/b.md").count(), 1);
        assert!(text.contains("```markdown\nledger revision"));
    }

    #[test]
    fn truncation_marker_at_1000_chars() {
        let long = "é".repeat(1500);
        let (_d, st) = store(&[("x", "wiki/x.md", &long)]);
        let text = bundle(&st, &long, true, false, 10, &|_, _| {}).unwrap().text;
        assert!(text.contains(&format!("```markdown\n{}\n...[truncated]...\n```\n", "é".repeat(1000))), "{}", &text[..200]);
        let short = "é".repeat(1000);
        let (_d2, st2) = store(&[("y", "wiki/y.md", &short)]);
        let t2 = bundle(&st2, &short, true, false, 10, &|_, _| {}).unwrap().text;
        assert!(t2.contains(&format!("```markdown\n{short}\n```\n")), "exactly 1000 characters is not truncated");
    }

    #[test]
    fn keyword_scores_print_two_decimals() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("wiki")).unwrap();
        std::fs::write(dir.path().join("wiki/k.md"), "alpha beta").unwrap();
        let st = SearchState::new(Config::resolve_from(Some(dir.path().to_path_buf()), |_| None));
        let text = bundle(&st, "alpha beta", false, false, 10, &|_, _| {}).unwrap().text;
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
        let text = bundle(&st, "the seed page", true, true, 10, &|_, _| {}).unwrap().text;
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

    /// Audit 2026-10-22 F6: the bundle names the index generation it was
    /// hydrated from and the graph generation it expanded. A promote landing
    /// between retrieval and the graph open changes neither label silently:
    /// the index stamp says `superseded`, the graph is the one actually opened.
    #[test]
    fn bundle_binds_index_and_graph_generations_across_a_promote_between_them() {
        use crate::store::{generation, MetaCache};
        let (_d, st) = store(&[("s", "wiki/seed.md", "the seed page")]);
        graph_db(&st.cfg.graph_db, &[("seed", "note", Some("Seed"), Some("wiki/seed.md")), ("d", "decision", Some("D"), Some("wiki/d.md"))], &[("seed", "d", "links_to")]);
        let index_before = generation(&st.cfg.search_db).unwrap();
        let cfg = st.cfg.clone();
        // Barrier: after hydration, before the graph open, promote BOTH stores.
        *st.after_hydrate.lock().unwrap() = Some(Box::new(move || {
            let next = cfg.search_db.with_extension("next");
            let cs = [Chunk { chunk_id: "s", source_path: "wiki/seed.md", heading: None, text: "the seed page", indexed_at: "2026-10-19T00:00:00", vector: mock("the seed page") }];
            search_db(&next, "mock", "deterministic_hash", &cs, &[("built_from_revision", "2")]);
            std::fs::rename(&next, &cfg.search_db).unwrap();
            let gnext = cfg.graph_db.with_extension("next");
            graph_db(&gnext, &[("seed", "note", Some("Seed"), Some("wiki/seed.md"))], &[]);
            std::fs::rename(&gnext, &cfg.graph_db).unwrap();
        }));
        let b = bundle(&st, "the seed page", true, true, 10, &|_, _| {}).unwrap();
        let Served::Index(m) = &b.served else { panic!("hybrid serves an index") };
        assert_eq!(m.generation, index_before, "the matches came from the pre-promote index");
        assert_eq!(m.built_from_revision, Some(1));
        let graph_after = generation(&st.cfg.graph_db).unwrap();
        assert_eq!(b.graph, Some(graph_after), "the graph opened is the one at the path when it was opened");
        assert!(!b.text.contains("Related Decisions"), "the promoted graph has no decision edge: {}", b.text);
        let cache = MetaCache::default();
        let stamp = crate::status::stamp_for(&st.cfg, &b.served, &cache, &crate::status::graph_part(&st.cfg, &cache, b.graph).into_iter().collect::<Vec<_>>());
        assert!(stamp.starts_with("[index rev 1 ") && stamp.contains("superseded") && stamp.ends_with("graph rev ?]"), "{stamp}");
    }
}
