from pathlib import Path
from jobscan.scan import scan_root, records_as_dicts

root = Path('examples/sample_export')
records = scan_root(root)
rows = records_as_dicts(records)
print(f"smoke records={len(rows)}")
