"""Generic product knowledge framework for the Estimating Assistant."""

from .product_catalog import ProductKnowledge, export_product_catalog_xlsx, load_product_catalog_json, write_product_catalog_json
from .product_matching import match_product, product_context_for_decision

__all__ = [
    "ProductKnowledge",
    "export_product_catalog_xlsx",
    "load_product_catalog_json",
    "match_product",
    "product_context_for_decision",
    "write_product_catalog_json",
]
