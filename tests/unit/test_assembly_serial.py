"""Unit tests for assembly serial_no support.

Covers:
- TAssembly ORM has serial_no column
- New sort key enum / literal additions (SERIAL_NO, DRAWING_NO, NAME)
- ErrCode.BIZ_ASSEMBLY_TOO_MANY_CHILDREN exists
"""
from __future__ import annotations

import enum

import pytest

from core.error_code import ErrCode
from model import TAssembly
from model.enums import PartSortKey, SortDir
from sqlalchemy import String


# ============================================================
# 1. TAssembly.serial_no column
# ============================================================


class TestAssemblyModelSchema:
    """TAssembly ORM exposes serial_no: String(8) nullable."""

    def test_serial_no_column_exists(self):
        cols = TAssembly.__table__.columns
        assert "serial_no" in cols, (
            "TAssembly must have serial_no column to store assembly-level serial"
        )

    def test_serial_no_is_string_8_nullable(self):
        col = TAssembly.__table__.columns["serial_no"]
        assert isinstance(col.type, String), (
            f"serial_no should be String type, got {type(col.type).__name__}"
        )
        assert col.type.length == 8, (
            f"serial_no String(8) to fit L1067-99 (9 chars max), got length={col.type.length}"
        )
        assert col.nullable is True, (
            "serial_no nullable; existing rows keep NULL after migration"
        )


# ============================================================
# 2. Sort key enums
# ============================================================


class TestSortKeyEnums:
    """PartSortKey now includes SERIAL_NO, DRAWING_NO, NAME."""

    def test_part_sort_key_has_new_values(self):
        expected = {"SERIAL_NO", "DRAWING_NO", "NAME"}
        actual = {m.name for m in PartSortKey}
        missing = expected - actual
        assert not missing, f"PartSortKey missing: {missing}"

    def test_part_sort_key_keeps_existing_values(self):
        assert PartSortKey.PLANNED_DELIVERY_DATE.value == "PLANNED_DELIVERY_DATE"
        assert PartSortKey.REQUEST_DATE.value == "REQUEST_DATE"
        assert PartSortKey.CREATED_AT.value == "CREATED_AT"

    def test_sort_dir_unchanged(self):
        assert SortDir.ASC.value == "ASC"
        assert SortDir.DESC.value == "DESC"


# ============================================================
# 3. AssemblySortKey — must live as a real enum in repository/assembly.py
# ============================================================


class TestAssemblySortKey:
    """Assembly sort keys should be a proper enum (not Literal) with new values."""

    def test_assembly_sort_key_is_enum(self):
        from repository.assembly import AssemblySortKey

        assert isinstance(AssemblySortKey, type) and issubclass(
            AssemblySortKey, enum.Enum
        ), "AssemblySortKey should be an Enum class"

    def test_assembly_sort_key_has_new_values(self):
        from repository.assembly import AssemblySortKey

        names = {m.name for m in AssemblySortKey}
        expected = {
            "PLANNED_DELIVERY_DATE",
            "REQUEST_DATE",
            "CREATED_AT",
            "SERIAL_NO",
            "DRAWING_NO",
            "NAME",
        }
        missing = expected - names
        assert not missing, f"AssemblySortKey missing: {missing}"


# ============================================================
# 4. Error code
# ============================================================


class TestErrorCode:
    """BIZ_ASSEMBLY_TOO_MANY_CHILDREN exists at 20303."""

    def test_too_many_children_code_exists(self):
        assert hasattr(ErrCode, "BIZ_ASSEMBLY_TOO_MANY_CHILDREN"), (
            "ErrCode.BIZ_ASSEMBLY_TOO_MANY_CHILDREN not declared"
        )

    def test_too_many_children_code_value(self):
        assert int(ErrCode.BIZ_ASSEMBLY_TOO_MANY_CHILDREN) == 20303
