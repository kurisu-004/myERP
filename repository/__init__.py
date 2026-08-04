from .applicant import ApplicantRepository
from .assembly import AssemblyRepository
from .customer import CustomerRepository
from .delivery_note import (
    DeliveryNoteCounterRepository,
    DeliveryNoteEventRepository,
    DeliveryNoteRepository,
)
from .menu import MenuRepository
from .outsource_company import OutsourceCompanyRepository
from .outsource_company_process import OutsourceCompanyProcessRepository
from .outsource_quote import OutsourceQuoteRepository
from .outsource_quote_event import OutsourceQuoteEventRepository
from .outsource_shipment import OutsourceShipmentRepository
from .part import PartRepository
from .part_batch import PartBatchRepository
from .part_event import PartEventRepository
from .part_file import PartFileRepository
from .pickup_skip_event import PickupSkipEventRepository
from .process import ProcessRepository
from .serial_counter import SerialCounterRepository
from .shelf import ShelfRepository
from .shelf_process import ShelfProcessRepository
from .statistics import StatisticsRepository
from .user import UserRepository, UserRoleRepository
from .work_type import WorkTypeRepository
from .work_type_process import WorkTypeProcessRepository
from .worker import WorkerRepository

__all__ = [
    "ApplicantRepository",
    "AssemblyRepository",
    "CustomerRepository",
    "DeliveryNoteCounterRepository",
    "DeliveryNoteEventRepository",
    "DeliveryNoteRepository",
    "MenuRepository",
    "OutsourceCompanyProcessRepository",
    "OutsourceCompanyRepository",
    "OutsourceQuoteEventRepository",
    "OutsourceQuoteRepository",
    "OutsourceShipmentRepository",
    "PartBatchRepository",
    "PartEventRepository",
    "PartFileRepository",
    "PartRepository",
    "PickupSkipEventRepository",
    "ProcessRepository",
    "SerialCounterRepository",
    "ShelfProcessRepository",
    "ShelfRepository",
    "StatisticsRepository",
    "UserRepository",
    "UserRoleRepository",
    "WorkTypeProcessRepository",
    "WorkTypeRepository",
    "WorkerRepository",
]
