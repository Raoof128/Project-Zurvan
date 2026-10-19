//! Tool-argument validation driven by the captured input schemas, reproducing
//! the SHAPE of the Python SDK's pydantic errors (a tool result, isError true,
//! "Error executing tool <name>: N validation error(s) for <name>Arguments").
//! Message wording may differ from pydantic's (plan decision); lax coercions
//! pydantic applies by default (numeric strings, "true"/"false") are honoured.

use serde_json::{Map, Value};

fn fmt_bound(v: &Value) -> String {
    match v {
        Value::Number(n) => n.to_string(),
        other => other.to_string(),
    }
}

fn type_name(t: &str) -> &'static str {
    match t {
        "string" => "a valid string",
        "integer" => "a valid integer",
        "number" => "a valid number",
        "boolean" => "a valid boolean",
        "array" => "a valid list",
        "object" => "a valid dictionary",
        _ => "valid",
    }
}

/// Coerce `v` to the schema's type in pydantic's lax mode, or say why not.
fn coerce(schema: &Value, v: &Value, field: &str, errors: &mut Vec<(String, String)>) -> Option<Value> {
    if let Some(variants) = schema.get("anyOf").and_then(Value::as_array) {
        if v.is_null() && variants.iter().any(|s| s.get("type") == Some(&Value::from("null"))) {
            return Some(Value::Null);
        }
        let first = variants.iter().find(|s| s.get("type") != Some(&Value::from("null")))?;
        return coerce(first, v, field, errors);
    }
    let ty = schema.get("type").and_then(Value::as_str).unwrap_or("");
    let fail = |errors: &mut Vec<(String, String)>, msg: String| {
        errors.push((field.to_string(), msg));
        None
    };
    let out = match ty {
        "string" => match v {
            Value::String(_) => v.clone(),
            _ => return fail(errors, format!("Input should be {}", type_name(ty))),
        },
        "integer" => match v {
            Value::Number(n) if n.is_i64() || n.is_u64() => v.clone(),
            Value::Number(n) if n.as_f64().is_some_and(|f| f.fract() == 0.0) => Value::from(n.as_f64().unwrap() as i64),
            Value::String(s) if s.trim().parse::<i64>().is_ok() => Value::from(s.trim().parse::<i64>().unwrap()),
            _ => return fail(errors, format!("Input should be {}", type_name(ty))),
        },
        "number" => match v {
            Value::Number(_) => v.clone(),
            Value::String(s) if s.trim().parse::<f64>().is_ok() => Value::from(s.trim().parse::<f64>().unwrap()),
            _ => return fail(errors, format!("Input should be {}", type_name(ty))),
        },
        "boolean" => match v {
            Value::Bool(_) => v.clone(),
            Value::Number(n) if n.as_i64() == Some(0) || n.as_i64() == Some(1) => Value::Bool(n.as_i64() == Some(1)),
            Value::String(s) => match s.to_ascii_lowercase().as_str() {
                "true" | "1" | "yes" | "y" | "on" | "t" => Value::Bool(true),
                "false" | "0" | "no" | "n" | "off" | "f" => Value::Bool(false),
                _ => return fail(errors, format!("Input should be {}", type_name(ty))),
            },
            _ => return fail(errors, format!("Input should be {}", type_name(ty))),
        },
        "array" => match v {
            Value::Array(items) => {
                let item_schema = schema.get("items").cloned().unwrap_or(Value::Null);
                let mut out = Vec::with_capacity(items.len());
                for (i, item) in items.iter().enumerate() {
                    if let Some(c) = coerce(&item_schema, item, &format!("{field}.{i}"), errors) {
                        out.push(c);
                    }
                }
                Value::Array(out)
            }
            _ => return fail(errors, format!("Input should be {}", type_name(ty))),
        },
        _ => v.clone(),
    };
    if let Some(choices) = schema.get("enum").and_then(Value::as_array) {
        if !choices.contains(&out) {
            let quoted: Vec<String> = choices.iter().map(|c| format!("'{}'", c.as_str().unwrap_or(""))).collect();
            let msg = match quoted.split_last() {
                Some((last, rest)) if !rest.is_empty() => format!("{} or {last}", rest.join(", ")),
                Some((last, _)) => last.clone(),
                None => String::new(),
            };
            return fail(errors, format!("Input should be {msg}"));
        }
    }
    if let (Some(x), Some(min)) = (out.as_f64(), schema.get("minimum")) {
        if x < min.as_f64().unwrap_or(f64::MIN) {
            return fail(errors, format!("Input should be greater than or equal to {}", fmt_bound(min)));
        }
    }
    if let (Some(x), Some(max)) = (out.as_f64(), schema.get("maximum")) {
        if x > max.as_f64().unwrap_or(f64::MAX) {
            return fail(errors, format!("Input should be less than or equal to {}", fmt_bound(max)));
        }
    }
    Some(out)
}

/// Validate `args` against `schema`, filling defaults. Unknown keys are
/// ignored (pydantic's default). Err carries the full tool-result text.
pub fn validate(tool: &str, schema: &Value, args: Option<&Map<String, Value>>) -> Result<Map<String, Value>, String> {
    let empty = Map::new();
    let args = args.unwrap_or(&empty);
    let props = schema.get("properties").and_then(Value::as_object).cloned().unwrap_or_default();
    let required: Vec<&str> = schema
        .get("required")
        .and_then(Value::as_array)
        .map(|r| r.iter().filter_map(Value::as_str).collect())
        .unwrap_or_default();
    let mut out = Map::new();
    let mut errors: Vec<(String, String)> = Vec::new();
    for (name, prop) in &props {
        match args.get(name) {
            Some(v) => {
                if let Some(c) = coerce(prop, v, name, &mut errors) {
                    out.insert(name.clone(), c);
                }
            }
            None if required.contains(&name.as_str()) => errors.push((name.clone(), "Field required".into())),
            None => {
                out.insert(name.clone(), prop.get("default").cloned().unwrap_or(Value::Null));
            }
        }
    }
    if errors.is_empty() {
        return Ok(out);
    }
    let n = errors.len();
    let mut text = format!(
        "Error executing tool {tool}: {n} validation error{} for {tool}Arguments",
        if n == 1 { "" } else { "s" }
    );
    for (field, msg) in errors {
        text.push_str(&format!("\n{field}\n  {msg}"));
    }
    Err(text)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn schema() -> Value {
        json!({"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string"},
            "hybrid": {"type": "boolean", "default": true},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            "max_queries": {"anyOf": [{"type": "integer", "minimum": 1, "maximum": 50}, {"type": "null"}], "default": null},
            "kind": {"type": "string", "enum": ["note", "todo", "risk"], "default": "note"},
            "tags": {"type": "array", "items": {"type": "string"}, "default": []}
        }})
    }

    #[test]
    fn fills_defaults_and_coerces_like_pydantic_lax() {
        let a = json!({"query": "x", "limit": "7", "hybrid": "false"});
        let out = validate("t", &schema(), a.as_object()).unwrap();
        assert_eq!(out["limit"], json!(7));
        assert_eq!(out["hybrid"], json!(false));
        assert_eq!(out["max_queries"], Value::Null);
        assert_eq!(out["tags"], json!([]));
    }

    #[test]
    fn errors_have_the_python_sdk_shape() {
        let e = validate("zurvan_search", &schema(), json!({"query": "x", "limit": 0}).as_object()).unwrap_err();
        assert_eq!(e, "Error executing tool zurvan_search: 1 validation error for zurvan_searchArguments\nlimit\n  Input should be greater than or equal to 1");
        let e = validate("t", &schema(), json!({"limit": 99, "kind": "bogus"}).as_object()).unwrap_err();
        assert!(e.starts_with("Error executing tool t: 3 validation errors for tArguments"), "{e}");
        assert!(e.contains("\nquery\n  Field required"));
        assert!(e.contains("\nkind\n  Input should be 'note', 'todo' or 'risk'"));
        let e = validate("t", &schema(), json!({"query": "x", "tags": ["a", 3]}).as_object()).unwrap_err();
        assert!(e.contains("\ntags.1\n  Input should be a valid string"), "{e}");
        assert!(validate("t", &schema(), json!({"query": "x", "max_queries": null}).as_object()).is_ok());
    }
}
