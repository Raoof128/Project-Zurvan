# Multi-Project Setup

You can register any valid Zurvan project using the CLI.

## Registering a Project

```bash
zurvan project register --name my-project --path /absolute/path/to/project
```

## Listing Projects

```bash
zurvan project list
```

## Switching Projects

Make a project the default target:

```bash
zurvan project use my-project
```

## Running Commands Against a Project

If you don't want to switch your default project, you can pass the `--project` flag to run a command against a specific one:

```bash
zurvan --project my-project search "query"
zurvan --project my-project doctor
zurvan --project my-project snapshot create
```
