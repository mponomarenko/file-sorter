# File Sorter

## Overview

The goal of this software is to handle large piles of files and backups that accumulate over the years, and facilitate cleanup and deduplication.

Inputs are a set of folders - like /Backups or a /mnt/NAS or ~/Documents.
Output is a copy, cleanen up and deduplicated - /Documents/Taxes, /Video/Movies, /Photos/2025/Hawaii Trip, /Documents/Projects/foo/

## Design

There few core issues this software deals with

- A whole section folder structure would be duplicated - backup of a backup
- Documents & Downloads can be completly unsorted - .doc, .zip, .txt, .pdf, .iso, .jpg etc
- Some folders have meaning - like a software source tree
- some folders are special and should be never touched, except to depuplicate the whole thing - .git, .venv, etc.

There is thus 2 core things we have to do

- Find largest possible duplicate - /Backup/Project A/Subfolder /Backup 2/Project A/Subfoder - the duplicate is entire "/Backup/Project A"
- once we have a folder - decide whether to keep it as is, or disaggregate
  - /Backup/July/Photo 2343.jpg... -> should be disaggregated into /Photos/2025/July/Photo 2343.jpg
  - /Backup/July/Hawaii Trip/Photo 2343.jpg... -> should be disaggregated into /Photos/2025/Hawaii Trip/Photo 2343.j
  - /Backup/Project A/Subfolder -> should not be disaggregated

### Folder-as-Archive

Some backups are compressed - /Backup.zip or /Backup/Foo.tar - this is not too important

- we can treat the whole archive as a folder, and deduplicate the contents
- we treat archives as folders that do not need to be disaggregated
- if some files within archive are duplicated but others are not - we can provide an option to delete files outside of archive.

### Detection Logic

For some things - its clear what to do (eg .venv) - for others its ambigous.

Here we start with a harcoded set of rules based on file path and mime and then provide them as a hint to LLM to classify.
LLM Rules are optional and can be removed.

All files are detected their Category and Subcategory.
The final folder structure is:

- /Category/Subgategory/File
- /Category/Subgategory/Non Disaggregated Folder
- /Category/Other Non Disaggregated Folder

## Safety and UX

- Non-destructive: Do not delete or modify any data; only record metadata.
- Transparent: Provide reports (future: summarize `folder_hashes` and duplicate groups).

## Rules & Custom metadata

rules.csv supports  named groups regexp matches such that a rule can match S3E2 and extract that as {seasion}/{episode}. See rules.csv for full feature list.

EXIF and Document files are supported as well.

## Rules System

### Rule Format

Rules are defined in CSV format with the following columns:

1. Path Glob - Regular expression pattern to match file paths
2. Mime Glob - MIME type pattern to match
3. Category Path - Target category for matched files
4. Folder Action - How to handle the containing folder
5. Classification Mode - Whether to use AI for further refinement

Example:

```csv
^.*\.pdf$,application/pdf,Documents/General,disaggregate,ai
```

### Named Capture Groups

Rules can use named capture groups in path patterns to extract metadata that influences categorization:

```csv
# Format: (?P<name>pattern)
^.*\/Backups\/(?P<backup_job>[A-Za-z0-9\-_]+_(?P<backup_year>\d{4})(?P<backup_month>\d{2})\d{2}_(?P<backup_time>\d{6}))(\/.*)?$
```

Supported named groups:

- `backup_job` - Backup job identifier
- `backup_year` - Year component (YYYY)
- `backup_month` - Month component (MM)
- `backup_time` - Time component (HHMMSS)
- `category` - Override default category
- `subcategory` - Override default subcategory

### Folder Actions

1. `keep` - Preserve folder structure
   - Used for organized collections (source code, photo albums)
   - Maintains project hierarchy
   - Prevents splitting cohesive folders
   - **IMPORTANT**: KEEP is inherited by ALL subfolders recursively
     - Once a folder is marked KEEP, all children are implicitly kept
     - No AI or rule evaluation happens for subfolders
     - Example: If `pidgin_portable/` is kept, then `pidgin_portable/Data/Documents/` is also kept (not disaggregated)

2. `keep_except` - Preserve folder but allow exceptions
   - Similar to `keep`, but children CAN be evaluated
   - Used for user homes: keep `/home/alice` but disaggregate `/home/alice/Documents`
   - Explicit rules or AI can mark children as `disaggregate`
   - Useful for structured folders with some organizational subfolders

3. `disaggregate` - Allow folder reorganization
   - Used for mixed content folders
   - Files can be moved to category-specific locations
   - Original structure may not be preserved
   - Each subfolder is independently evaluated

### Classification Modes

1. `final` - Rule-based decision is final
   - No further classification attempted
   - Used for well-defined patterns
   - Examples: .git folders, source code files

2. `ai` - Allow AI refinement
   - Initial category can be refined by AI
   - Used for ambiguous content
   - Examples: documents, archives, generic files

### Rule Processing

Rules are processed in order, with important considerations:

1. More specific patterns should come first
2. System and metadata patterns take precedence
3. File extension patterns come next
4. MIME type patterns follow
5. Generic fallback patterns come last

### Folder Processing Order

Folders are always processed top-down (parent before children):

1. `/mnt` is evaluated first
2. Then `/mnt/c`
3. Then `/mnt/c/Users`
4. And so on...

**Critical behavior:**
- Each folder is evaluated at most ONCE
- If a parent folder is marked KEEP, children are never evaluated (inheritance)
- AI is never called twice for the same folder
- AI is never called for subfolders of kept folders

### Special Patterns

Here are some examples of common pattern types:

System Files:

```csv
^.*\/\.git\/.*$,*,System/Metadata,keep,final
```

Media Files with Show Detection:

```csv
^.*(s\d{1,2}e\d{1,2}|season).*\.(mp4|mkv)$,video/.+,Media/Videos/Shows,keep,final
```

Smart Document Classification:

```csv
^.*(invoice|receipt).*\.pdf$,*,Documents/Finance,disaggregate,ai
```

### Best Practices

1. Always test new rules with sample data
2. Use named capture groups for structured data
3. Consider MIME types for better accuracy
4. Order rules from specific to general
5. Use `ai` mode when classification is uncertain
6. Use `keep` for meaningful directory structures
7. Use `disaggregate` for mixed-content folders

## Folder Disaggregation Algorithm

The idea is to act like a human would

1. open a folder - does this sound like something that is coherent?

- some special files ".git", ".svn", vscode or other system project files - this is a coherent project.
- is this some kind of group that share the same contet? music album, TV series, etc - should stay
- is this clearly a file dump? "Documnets" and "Downloads" folder usually get there
- Google Drive, Drop Box, etc.. cloud storage roots - need to be disaggregated almost always
- a set of document files for a some kind of project? with file names having some kind of
- installed software folder (why did this software got pointed to it?)
- Backups - this is tough. its a wild mix of whole wholders and random files. Top level needs to be split, but internal levesl should not be.

Generally for a given folder: /mnt/src/src1/foo/bar/Backups/old/take1/Taxes 2025/w2.pdf

- /mnt/src/src1 - this is the source path - we always need to disaggregate it.
- foo, foo/bar, foo/bar/Backups, foo/bar/Backups/old/ - disaggregate.
- take1 - depends. likely disaggregate
- Taxes 2025 - likeky keep, at the same time, this is same as our final Documents/Taxes/2025 - so in this case it shold be disaggregated, if it was "Firm X Tax Review" - then it should have been kept as a thing: Documents/Taxes/2025/Firm X Tax Review/w1.pdf, so:
  /mnt/src/src1/foo/bar/Backups/old/take1/Firm X Tax Review/w2.pdf ->
  /mnt/out/Documents/Taxes/2025/Firm X Tax Review/w2.pdf

### Processing Algorithm

**Critical Rule**: Folders are processed parent-first (top-down), never child-first.

**Order**: /mnt → /mnt/c → /mnt/c/Users → /mnt/c/Users/Documents → ...

**Inheritance**: When a folder is marked KEEP, ALL children automatically inherit KEEP.
- Children are never classified by AI or rules
- Entire subtree is preserved as a unit
- No need to evaluate nested folders
- **Exception**: `keep_except` allows children to be evaluated (rules/AI can override)

**Processing Steps**:
1. Sort all folders by path depth (shallowest first)
2. For each folder in order:
   - If parent was KEEP → inherit KEEP (skip classification)
   - If parent was KEEP_EXCEPT → run classifier chain (children can override)
   - Otherwise, run classifier chain: Rules → AI → Default
3. Never classify the same folder twice
4. Never classify children of kept folders (except keep_except)

**Example - Regular KEEP**:
```
/backup/projects/my-app       → KEEP (by AI)
/backup/projects/my-app/src   → KEEP (inherited)
/backup/projects/my-app/tests → KEEP (inherited)
... all nested folders automatically KEEP
```

**Example - KEEP_EXCEPT**:
```
/home/alice                   → KEEP_EXCEPT (by rule)
/home/alice/.config           → KEEP (inherited, no explicit rule)
/home/alice/Documents         → DISAGGREGATE (explicit rule override)
/home/alice/Documents/Work    → evaluated independently
```

## Future Enhancements

- Reading inside archives
- Automatic deletion or reflinking of duplicate folders

## Not Implemented (for now)

- Reading inside archives.
- Automatic deletion or reflinking of duplicate folders.
- we can allow rule matching regexps to FILL metadata as well. if the user knows for a fact some folder
  structure is Auhtor/Album, and its alreaddy matched by regexp - we can fill it - we already have {Season}
  and {Eposide} S02E24 regexp matching. Just need to figure out how to have named regexp () match patterns.
