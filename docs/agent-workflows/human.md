# Human Workflow

You can use Zurvan's orchestration tools even if you aren't an AI agent! It acts as a great project manager.

## 1. Start your day
```bash
zurvan session start --topic "Refactoring the database"
```
This gives you a blank Markdown file in `wiki/sessions/`. Open it in Obsidian and use it as your daily scratchpad.

## 2. Get Context
```bash
zurvan agent preflight --topic "database schema"
```
Read the output to see what decisions and claims are already recorded about the database.

## 3. Finish up
```bash
zurvan agent postedit --summary "Updated the users table" --files db/schema.sql --checks "make test"
zurvan session close --topic "Refactoring the database" --summary "Done for the day" --checks "make test"
```
