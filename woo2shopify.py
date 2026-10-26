"""Convert a WooCommerce product export CSV into a Shopify product import CSV.

Handles simple and variable products (up to 3 options), sale prices, stock,
weight, categories/tags and multiple images.

Usage:
    python woo2shopify.py woo_export.csv shopify_import.csv [--vendor "My Store"]
"""

import argparse
import csv
import re
import sys
import unicodedata

SHOPIFY_COLUMNS = [
    "Handle", "Title", "Body (HTML)", "Vendor", "Type", "Tags", "Published",
    "Option1 Name", "Option1 Value", "Option2 Name", "Option2 Value", "Option3 Name", "Option3 Value",
    "Variant SKU", "Variant Grams", "Variant Inventory Tracker", "Variant Inventory Qty",
    "Variant Inventory Policy", "Variant Fulfillment Service", "Variant Price", "Variant Compare At Price",
    "Variant Requires Shipping", "Variant Taxable", "Image Src", "Image Position", "Variant Image", "Status",
]
MAX_OPTIONS = 3


def slugify(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "product"


def split_list(value):
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def to_grams(weight_kg):
    try:
        return str(round(float(weight_kg) * 1000))
    except (TypeError, ValueError):
        return ""


def attributes(row):
    """Return [(name, value_string), ...] from 'Attribute N name' / 'Attribute N value(s)' columns."""
    attrs = []
    n = 1
    while f"Attribute {n} name" in row:
        name = (row.get(f"Attribute {n} name") or "").strip()
        if name:
            attrs.append((name, (row.get(f"Attribute {n} value(s)") or "").strip()))
        n += 1
    return attrs


def prices(row):
    regular = (row.get("Regular price") or "").strip()
    sale = (row.get("Sale price") or "").strip()
    if sale and regular and float(sale) < float(regular):
        return sale, regular
    return regular or sale, ""


def variant_fields(row):
    price, compare_at = prices(row)
    stock = (row.get("Stock") or "").strip()
    tracked = stock != ""
    return {
        "Variant SKU": (row.get("SKU") or "").strip(),
        "Variant Grams": to_grams(row.get("Weight (kg)")),
        "Variant Inventory Tracker": "shopify" if tracked else "",
        "Variant Inventory Qty": str(int(float(stock))) if tracked else "",
        "Variant Inventory Policy": "continue" if (row.get("Backorders allowed?") or "0").strip() in ("1", "notify") else "deny",
        "Variant Fulfillment Service": "manual",
        "Variant Price": price,
        "Variant Compare At Price": compare_at,
        "Variant Requires Shipping": "FALSE" if "virtual" in (row.get("Type") or "") else "TRUE",
        "Variant Taxable": "FALSE" if (row.get("Tax status") or "").strip() == "none" else "TRUE",
    }


def product_type_and_tags(row):
    # WooCommerce categories look like "Clothing > Shirts, Sale"
    categories = [c.split(">")[-1].strip() for c in split_list(row.get("Categories"))]
    tags = split_list(row.get("Tags"))
    product_type = categories[0] if categories else ""
    all_tags = list(dict.fromkeys(tags + categories))
    return product_type, ", ".join(all_tags)


def convert(rows, vendor):
    parents = [r for r in rows if "variation" not in (r.get("Type") or "")]
    by_id = {(r.get("ID") or "").strip(): r for r in parents}
    by_sku = {(r.get("SKU") or "").strip(): r for r in parents if (r.get("SKU") or "").strip()}

    variations = {}
    orphans = 0
    for r in rows:
        if "variation" not in (r.get("Type") or ""):
            continue
        ref = (r.get("Parent") or "").strip()
        parent = by_id.get(ref[3:]) if ref.startswith("id:") else by_sku.get(ref)
        if parent is None:
            orphans += 1
            continue
        variations.setdefault(id(parent), []).append(r)

    used_handles = set()
    out = []
    for p in parents:
        handle = base = slugify(p.get("Name") or "")
        i = 2
        while handle in used_handles:
            handle = f"{base}-{i}"
            i += 1
        used_handles.add(handle)

        product_type, tags = product_type_and_tags(p)
        published = (p.get("Published") or "").strip() == "1"
        images = split_list(p.get("Images"))
        base_row = {
            "Handle": handle,
            "Title": (p.get("Name") or "").strip(),
            "Body (HTML)": (p.get("Description") or p.get("Short description") or "").strip(),
            "Vendor": vendor,
            "Type": product_type,
            "Tags": tags,
            "Published": "TRUE" if published else "FALSE",
            "Status": "active" if published else "draft",
        }

        kids = variations.get(id(p), [])
        if kids:
            option_names = [name for name, _ in attributes(p)][:MAX_OPTIONS]
            variant_rows = []
            for k in kids:
                values = dict(attributes(k))
                v = {}
                for n, name in enumerate(option_names, start=1):
                    v[f"Option{n} Name"] = name
                    v[f"Option{n} Value"] = values.get(name, "") or "Any"
                v.update(variant_fields(k))
                kid_images = split_list(k.get("Images"))
                if kid_images:
                    v["Variant Image"] = kid_images[0]
                    if kid_images[0] not in images:
                        images.append(kid_images[0])
                variant_rows.append(v)
        else:
            variant_rows = [{"Option1 Name": "Title", "Option1 Value": "Default Title", **variant_fields(p)}]

        # First row carries product fields; following variant rows carry only Handle + variant fields.
        for n, v in enumerate(variant_rows):
            row = dict(base_row) if n == 0 else {"Handle": handle}
            if n > 0:
                # Shopify only reads option names from the first row.
                v = {k: val for k, val in v.items() if not k.endswith(" Name")}
            row.update(v)
            if n < len(images):
                row["Image Src"] = images[n]
                row["Image Position"] = str(n + 1)
            out.append(row)
        for n in range(len(variant_rows), len(images)):
            out.append({"Handle": handle, "Image Src": images[n], "Image Position": str(n + 1)})

    return out, len(parents), orphans


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="WooCommerce product export CSV")
    ap.add_argument("output", help="Shopify import CSV to write")
    ap.add_argument("--vendor", default="", help="Vendor name for all products")
    args = ap.parse_args()

    with open(args.input, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    out, product_count, orphans = convert(rows, args.vendor)

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SHOPIFY_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(out)

    print(f"Converted {product_count} products into {len(out)} Shopify rows -> {args.output}")
    if orphans:
        print(f"Warning: {orphans} variation(s) skipped because their parent product was not found", file=sys.stderr)


if __name__ == "__main__":
    main()
