# woo2shopify

Converts a WooCommerce product export (Products → Export → CSV) into a CSV that Shopify's
product importer accepts (Products → Import).

- Simple and variable products (up to 3 options)
- Variations linked by `id:123` or by parent SKU
- Sale price → Price + Compare-at price
- Stock, backorders, weight (kg → grams), tax status, virtual products (no shipping)
- Categories → product type + tags; all product and variation images
- Unpublished products → Shopify drafts

```bash
python woo2shopify.py woo_export.csv shopify_import.csv --vendor "My Store"
```

Requires Python 3.8+, no dependencies. Try it with `sample_woo_export.csv`.
