"""Schema package.

Orchestrates forward-reference resolution across modules to break the
circular import between ``schema.part`` and ``schema.assembly`` at
module-load time.

``PartBatchTree*`` models in ``schema.part`` use string forward references
to ``"AssemblyOut"`` (``schema/assembly.py``) and ``"PartFileOut"``
(``schema/part_file.py``). A naive bottom-of-file rebuild in
``schema.part`` hit ``ImportError`` when ``schema.assembly`` was loaded
first, because ``AssemblyOut`` is defined at ``schema/assembly.py:16``
*after* the back-import at line 12 — a previous ``try/except
ImportError`` silently swallowed that and produced
``PydanticUndefinedAnnotation: name 'AssemblyOut' is not defined`` from
the following ``model_rebuild``.

Resolving those refs here, after all three modules are guaranteed loaded,
gives a deterministic load order regardless of entry point
(``service.assembly`` / ``service.part`` / ``api.v1.assembly`` / tests
all funnel through this file).
"""

from schema.assembly import AssemblyOut
from schema.part_file import PartFileOut
from schema.part import (
    PartBatchTreeAssemblyResult,
    PartBatchTreePartResult,
    PartBatchTreeResult,
)

_REBUILD_NS = {"AssemblyOut": AssemblyOut, "PartFileOut": PartFileOut}
PartBatchTreePartResult.model_rebuild(_types_namespace=_REBUILD_NS)
PartBatchTreeAssemblyResult.model_rebuild(_types_namespace=_REBUILD_NS)
PartBatchTreeResult.model_rebuild()
