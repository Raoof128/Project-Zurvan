# Project Registry

The project registry is stored locally at `~/.zurvan/projects.json`.

**Never commit this file to a public repository.**

## Example Shape

```json
{
  "current": "zurvan",
  "projects": {
    "zurvan": {
      "path": "/absolute/local/path/to/project",
      "created_at": "2026-05-31T10:00:00+10:00",
      "updated_at": "2026-05-31T10:00:00+10:00"
    }
  }
}
```

## Troubleshooting

If the registry becomes corrupted, you will see a `Registry is corrupted` error. You can either fix the JSON syntax manually or delete the file and re-register your projects.
