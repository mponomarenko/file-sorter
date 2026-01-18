# file-sorter
LLM-backed file and folder categorizer for backup deduplication and NAS cleanup

## Overview

File Sorter analyzes large piles of files and backups accumulated over the years, facilitating cleanup and deduplication. It processes folders containing mixed content and outputs a clean, organized, deduplicated structure.

**Key Features:**
- **Duplicate Detection**: Finds largest possible duplicates at folder level
- **Smart Disaggregation**: Decides whether to keep folder structure or reorganize contents
- **Rule-Based Classification**: Pattern matching with MIME types and named capture groups
- **Optional AI Enhancement**: LLM-based refinement for ambiguous cases
- **Non-Destructive**: Only records metadata, never modifies source files
- **Docker-Based**: Runs in containerized environment with all dependencies

## How It Works

**Input**: Source folders like `/Backups`, `/mnt/NAS`, or `~/Documents`

**Output**: Organized structure like:
- `/Documents/Taxes/2025/...`
- `/Video/Movies/...`
- `/Photos/2025/Hawaii Trip/...`
- `/Documents/Projects/foo/...`

**Processing:**
1. Scans all files and folders
2. Matches against rules (path patterns, MIME types)
3. Optionally refines classification with AI
4. Determines folder actions (keep vs disaggregate)
5. Generates reorganization plan

## Folder Actions

- **keep**: Preserve entire folder structure (e.g., source code projects, organized collections)
  - All subfolders automatically inherit KEEP
- **keep_except**: Preserve folder but allow children to be evaluated (e.g., user home directories)
- **disaggregate**: Allow reorganization (e.g., mixed content folders like Downloads)

## Running and Testing

### Quick Test
```bash
./test.sh
```

### Full Public Test
```bash
./full_test.sh
```
Uses repository fixtures for comprehensive testing.

### Private Test
```bash
./tests/private/full_test_private.sh
```
Requires private paths configured locally.

### Docker-Based Testing
Note: Docker-based tests may take significant time for image building and dependency downloads.

### Running the Application
The application is designed to run as a Docker container. See `docker-compose.yml` for configuration.

```bash
docker-compose up
```

## Configuration

- **Rules**: Defined in [app/data/rules.csv](app/data/rules.csv) with pattern matching and folder actions
- **Categories**: Defined in [app/data/categories.csv](app/data/categories.csv)
- **Prompts**: AI classification prompts in [prompts/](prompts/) directory

## Design Details

For comprehensive design documentation, architecture, and algorithms, see [File-Sorter.md](File-Sorter.md).

Key topics covered:
- Folder-as-Archive handling
- Detection logic and rules system
- Named capture groups for metadata extraction
- Folder disaggregation algorithm
- Processing order (parent-first, top-down)
- Safety and transparency guarantees
