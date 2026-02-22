import os
import sys

# Add project root to sys.path if needed
sys.path.append(os.getcwd())

from utils.ocr import resolve_image_path, ATTACHMENTS_DIR

print(f"CWD: {os.getcwd()}")
print(f"ATTACHMENTS_DIR: {ATTACHMENTS_DIR}")

test_img = "order_error.png"
resolved = resolve_image_path(test_img)
print(f"Resolved '{test_img}': {resolved}")

if resolved:
    print(f"Exists: {os.path.exists(resolved)}")
    print(f"Is Absolute: {os.path.isabs(resolved)}")
else:
    print(f"Failed to resolve '{test_img}'")
