#!/usr/bin/env python3
"""
Dump database contents for a given folder path.
Shows all files and folder actions for the specified path and its children.
"""

import argparse
import sqlite3
import sys
from pathlib import Path


def dump_folder_info(db_path: str, folder_path: str, verbose: bool = False):
    """Dump all database information for a folder and its contents."""
    if not Path(db_path).exists():
        print(f"Error: Database not found at {db_path}", file=sys.stderr)
        return 1
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Normalize folder path - handle root "/" specially
    if folder_path == "/":
        folder_path_pattern = "%"
        folder_exact = "/"
    else:
        folder_path = folder_path.rstrip('/')
        folder_path_pattern = f"{folder_path}/%"
        folder_exact = folder_path
    
    print(f"=== Database Dump for: {folder_exact} ===\n")
    
    # 1. Folder Actions
    print("--- Folder Actions ---")
    
    # Check if table exists
    cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name='folder_actions'
    """)
    has_folder_actions = cursor.fetchone() is not None
    
    if has_folder_actions:
        if folder_exact == "/":
            cursor.execute("""
                SELECT folder_path, action, decision_source, 
                       datetime(decided_at, 'unixepoch') as decided_at
                FROM folder_actions
                ORDER BY folder_path
            """)
        else:
            cursor.execute("""
                SELECT folder_path, action, decision_source, 
                       datetime(decided_at, 'unixepoch') as decided_at
                FROM folder_actions
                WHERE folder_path = ? OR folder_path LIKE ?
                ORDER BY folder_path
            """, (folder_exact, folder_path_pattern))
        
        folder_rows = cursor.fetchall()
        if folder_rows:
            for row in folder_rows:
                print(f"\nFolder: {row['folder_path']}")
                print(f"  Action: {row['action']}")
                print(f"  Decision: {row['decision_source']}")
                print(f"  Decided: {row['decided_at']}")
        else:
            print("  (No folder actions found)")
    else:
        print("  (folder_actions table not found - run with updated code to create)")
    
    # 2. Files in this folder
    print(f"\n--- Files ---")
    if folder_exact == "/":
        cursor.execute("""
            SELECT path, size, mime, hash, category, rule_category, ai_category, 
                   dest, status, note
            FROM files
            ORDER BY length(path) - length(replace(path, '/', '')), path
        """)
    else:
        cursor.execute("""
            SELECT path, size, mime, hash, category, rule_category, ai_category, 
                   dest, status, note
            FROM files
            WHERE path = ? OR path LIKE ?
            ORDER BY length(path) - length(replace(path, '/', '')), path
        """, (folder_exact, folder_path_pattern))
    
    file_rows = cursor.fetchall()
    if file_rows:
        total_size = 0
        status_counts: dict[str, int] = {}
        category_counts: dict[str, int] = {}
        folder_action_by_file = {}
        
        for row in file_rows:
            total_size += row['size'] or 0
            status_counts[row['status']] = status_counts.get(row['status'], 0) + 1
            cat = row['category'] or 'None'
            category_counts[cat] = category_counts.get(cat, 0) + 1
            
            # Determine effective folder action for this file
            if has_folder_actions:
                file_path = row['path']
                file_folder = str(Path(file_path).parent)
                cursor.execute("""
                    SELECT action FROM folder_actions 
                    WHERE ? LIKE folder_path || '/%' OR ? = folder_path
                    ORDER BY length(folder_path) DESC
                    LIMIT 1
                """, (file_path, file_path))
                action_row = cursor.fetchone()
                if action_row:
                    folder_action_by_file[file_path] = action_row['action']
            
            if verbose:
                print(f"\n{row['path']}")
                print(f"  Size: {row['size']:,} bytes")
                print(f"  MIME: {row['mime']}")
                print(f"  Hash: {row['hash'][:16]}..." if row['hash'] else "  Hash: None")
                print(f"  Status: {row['status']}")
                print(f"  Category: {row['category']}")
                if row['rule_category']:
                    print(f"  Rule Category: {row['rule_category']}")
                if row['ai_category']:
                    print(f"  AI Category: {row['ai_category']}")
                if row['dest']:
                    print(f"  Destination: {row['dest']}")
                if row['note']:
                    print(f"  Note: {row['note']}")
                if row['path'] in folder_action_by_file:
                    print(f"  Folder Action: {folder_action_by_file[row['path']]} (inherited)")
        
        print(f"\nTotal Files: {len(file_rows)}")
        print(f"Total Size: {total_size:,} bytes ({total_size / (1024**3):.2f} GiB)")
        
        print("\nStatus Distribution:")
        for status, count in sorted(status_counts.items()):
            print(f"  {status}: {count}")
        
        # Show folder action distribution
        if folder_action_by_file:
            action_counts: dict[str, int] = {}
            for action in folder_action_by_file.values():
                action_counts[action] = action_counts.get(action, 0) + 1
            print("\nFolder Action Distribution:")
            for action, count in sorted(action_counts.items()):
                print(f"  {action}: {count}")
        
        print("\nCategory Distribution:")
        for category, count in sorted(category_counts.items(), key=lambda x: -x[1])[:10]:
            print(f"  {category}: {count}")
        if len(category_counts) > 10:
            print(f"  ... ({len(category_counts) - 10} more categories)")
    else:
        print("  (No files found)")
    
    # 3. Summary
    print("\n--- Summary ---")
    
    # Check if files are skipped due to parent KEEP
    if has_folder_actions:
        if folder_exact == "/":
            cursor.execute("""
                SELECT COUNT(*) as count
                FROM files f
                WHERE f.category IS NULL
                  AND f.status = 'scanned'
                  AND EXISTS (
                    SELECT 1 FROM folder_actions fa
                    WHERE fa.action = 'keep'
                      AND (f.path LIKE fa.folder_path || '/%')
                  )
            """)
        else:
            cursor.execute("""
                SELECT COUNT(*) as count
                FROM files f
                WHERE (f.path = ? OR f.path LIKE ?)
                  AND f.category IS NULL
                  AND f.status = 'scanned'
                  AND EXISTS (
                    SELECT 1 FROM folder_actions fa
                    WHERE fa.action = 'keep'
                      AND (f.path LIKE fa.folder_path || '/%')
                  )
            """, (folder_exact, folder_path_pattern))
        
        skipped_count = cursor.fetchone()['count']
        if skipped_count > 0:
            print(f"Files skipped (parent KEEP): {skipped_count}")
            print("  (These files inherit their parent folder's action)")
    else:
        print("(Folder action inheritance not available - table missing)")
    
    conn.close()
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Dump database contents for a given folder path",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /work/catalog.sqlite /sources/src1/Documents
  %(prog)s ~/catalog.sqlite /sources/src1/pidgin_portable --verbose
  %(prog)s ./catalog.sqlite /sources/src1 -v
  %(prog)s ./catalog.sqlite / -v    # Dump entire database
        """
    )
    parser.add_argument("database", help="Path to SQLite database file")
    parser.add_argument("folder", help="Folder path to dump (absolute path, use '/' for all)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show detailed information for each file including inherited folder actions")
    
    args = parser.parse_args()
    
    return dump_folder_info(args.database, args.folder, args.verbose)


if __name__ == "__main__":
    sys.exit(main())
