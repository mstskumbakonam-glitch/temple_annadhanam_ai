"""Concurrency-safe display-code generation.

visitor_code (VIS-000001) and staff_code (STAFF-001) are produced by PostgreSQL
sequences used as column DEFAULTs. Two concurrent INSERTs can never receive the
same code, because nextval() is atomic and is not rolled back.

MAX(id) + 1 is never used: it races under concurrency and reuses codes after
deletions.
"""

from sqlalchemy import Sequence, text
from sqlalchemy.sql.elements import TextClause

VISITOR_CODE_SEQUENCE = "visitor_code_seq"
STAFF_CODE_SEQUENCE = "staff_code_seq"

# start=1 so the first visitor is VIS-000001 and the first staff member STAFF-001.
visitor_code_seq = Sequence(VISITOR_CODE_SEQUENCE, start=1, increment=1)
staff_code_seq = Sequence(STAFF_CODE_SEQUENCE, start=1, increment=1)


def visitor_code_default() -> TextClause:
    """SQL DEFAULT producing VIS-000001, zero-padded to six digits.

    Written in the canonical form PostgreSQL stores (explicit ::text / ::regclass
    casts and outer parentheses) so that `alembic revision --autogenerate`
    compares it as unchanged instead of emitting a phantom alter_column.
    """
    return text(
        f"('VIS-'::text || lpad((nextval('{VISITOR_CODE_SEQUENCE}'::regclass))::text,"
        " 6, '0'::text))"
    )


def staff_code_default() -> TextClause:
    """SQL DEFAULT producing STAFF-001, zero-padded to three digits.

    Padding is a minimum width, so the sequence keeps working past STAFF-999
    (the next code is simply STAFF-1000).
    """
    return text(
        f"('STAFF-'::text || lpad((nextval('{STAFF_CODE_SEQUENCE}'::regclass))::text,"
        " 3, '0'::text))"
    )
