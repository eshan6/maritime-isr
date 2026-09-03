"""Arrival notifications: unstructured documents in, structured records out.

*"The ask is an application that converts this and other unstructured sources
into structured records, fuses them to AIS data to generate risk intelligence,
and remains compatible with the electronic version expected from the national
logistics portal in future."* — the IDEX Challenge 82 brief, Area 4.

Three layers, and the split is the point:

* `readers`  — bytes to text, one reader per format. The only place that knows
  what a PDF is. Every reader returns the same thing: a list of *passages*, each
  with a locator an operator can find by eye and a confidence the reader earned.
* `extract`  — passages to fields. Knows about labels and synonyms and nothing
  about file formats. This is where "Last Port of Call", "From" and "Previous
  Port" become one field.
* `resolve`  — fields to a vessel. Knows about identifiers and names and hedges
  in the direction of refusing, because a notification attached to the wrong
  hull is worse than one attached to none.

**The electronic feed is a fourth reader and not a fourth pipeline.** That is
how the requirement's compatibility clause is honoured: the portal's structured
JSON enters at the same seam as a scanned fax, produces the same record, and is
distinguishable only by the per-field confidence and method — which is where the
difference genuinely lives.
"""
from __future__ import annotations

from .extract import extract_notification, FIELD_PATTERNS
from .kinds import classify_document, kind_label
from .readers import Passage, READERS, read_document, reader_availability
from .resolve import resolve_notification
from .service import vessel_documents

__all__ = ["Passage", "READERS", "read_document", "reader_availability",
           "extract_notification", "FIELD_PATTERNS", "resolve_notification",
           "classify_document", "kind_label", "vessel_documents"]
