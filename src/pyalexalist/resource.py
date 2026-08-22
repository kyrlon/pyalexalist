
import uuid
import datetime
import enum
from collections.abc import Callable
from operator import attrgetter

from .exception import DefaultListModificationException


class ItemCheckedValue(int, enum.Enum):
    """Alexa item status enum; int-compatible for comparisons, with a string label for API calls."""

    def __new__(cls, value, label):
        # Inspired by https://stackoverflow.com/a/68400507/13642249
        obj = int.__new__(cls, value)
        obj._value_ = value
        obj.label = label
        return obj

    UNCHECKED = (0, 'ACTIVE')
    CHECKED = (1, 'COMPLETE')

    def to_str(self) -> str:
        return self.label

    @classmethod
    def from_str(cls, input_str: str) -> "ItemCheckedValue":
        """Return the matching enum member by case-insensitive label comparison.

        Args:
            input_str: Alexa status string, e.g. 'ACTIVE' or 'COMPLETE'.
        Returns:
            The matching ItemCheckedValue member.
        Raises:
            ValueError: If no member matches input_str.
        """
        for member in cls:
            if member.label.casefold() == input_str.casefold():
                return member
        raise ValueError(f"{cls.__name__} has no value matching {input_str}")

    @classmethod
    def from_int(cls, input_int: int | bool) -> str:
        """Return the label string for the given integer value.

        Args:
            input_int: 0 for UNCHECKED, 1 for CHECKED; bool is also accepted.
        Returns:
            The label string ('ACTIVE' or 'COMPLETE').
        Raises:
            ValueError: If no member matches input_int.
        """
        for member in cls:
            if member.value == input_int:
                return member.label
        raise ValueError(f"{cls.__name__} has no value matching {input_int}")


class Resource:
    """Base class providing a UUID, created/updated timestamps, and a set of dirty field names."""

    __slots__ = ("_id","_createdTime", "_updatedTime", "dirty_fields" )

    def __init__(self) -> None:
        self._id = self.generateId()
        self._createdTime = None
        self._updatedTime = None
        self.dirty_fields: set[str] = set()

    @classmethod
    def generateId(cls) -> str:
        return str(uuid.uuid4())

    @classmethod
    def int_to_dt(cls, t: float | int) -> datetime.datetime:
        """Converts a timestamp to UTC datetime, auto-detecting milliseconds by comparing against the current epoch second.

        Args:
            t: Unix timestamp in seconds or milliseconds.
        Returns:
            datetime.
        """
        if t > datetime.datetime.now(datetime.timezone.utc).timestamp():
            t = t / 1000
        return datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc)

    @property
    def id(self) -> str:
        return self._id

    @property
    def createdTime(self) -> "datetime.datetime | None":
        return self._createdTime

    @createdTime.setter
    def createdTime(self, value: "datetime.datetime | int | float"):
        if isinstance(value, (int, float)):
            value = self.int_to_dt(value)
        self._createdTime = value

    @property
    def updatedTime(self) -> "datetime.datetime | None":
        return self._updatedTime

    @updatedTime.setter
    def updatedTime(self, value: "datetime.datetime | int | float"):
        if isinstance(value, (int, float)):
            value = self.int_to_dt(value)
        self._updatedTime = value


class ListItem(Resource):
    """Alexa list item with dirty-tracking properties and a server snapshot for no-op push detection."""

    __slots__ = (
        "_itemId",
        "_itemName",
        "_itemStatus",
        "_quantity",
        "_note",
        "_version",
        "_deleted",
        "_server_itemName",
        "_server_itemStatus",
        "_server_quantity",
        "_server_note",
    )

    def __init__(self, itemName: str = "", checked: bool = False, quantity: int | None = None, note: str | None = None) -> None:
        super().__init__()
        self._itemId = None
        self._itemName = itemName
        self._itemStatus = ItemCheckedValue.CHECKED if checked else ItemCheckedValue.UNCHECKED
        self._quantity = None
        self.quantity = quantity
        self._note = note
        self._version = None
        self._deleted = None
        self._server_itemName = None
        self._server_itemStatus = None
        self._server_quantity = None
        self._server_note = None

    def load(self, raw_item: dict, clean: bool = True) -> None:
        """Populate fields from a raw Alexa API item dict and optionally snapshot server state.

        Args:
            raw_item: API response dict with keys itemName, itemStatus, itemId, etc.
            clean: If True (default), snapshot server state and clear dirty flags after loading.
        """
        self.itemName = raw_item["itemName"]
        self.checked = ItemCheckedValue.from_str(raw_item["itemStatus"])
        self._itemId = raw_item["itemId"]
        self.createdTime = raw_item["createAt"]
        self.updatedTime = raw_item["updateAt"]
        self._version = raw_item["version"]
        self.quantity = raw_item["quantity"]
        self.note = raw_item.get("note")
        if clean:
            self.clean()

    def delete(self) -> None:
        self._deleted = True

    def undelete(self) -> None:
        self._deleted = False
    
    @property
    def deleted(self) -> "bool | None":
        return self._deleted

    @property
    def itemId(self) -> "str | None":
        return self._itemId

    @property
    def itemName(self) -> str:
        return self._itemName

    @itemName.setter
    def itemName(self, value: str):
        self._itemName = value.strip()
        self.dirty_fields.add("itemName")

    @property
    def checked(self) -> "ItemCheckedValue":
        return self._itemStatus

    @checked.setter
    def checked(self, value: "ItemCheckedValue | bool"):
        if isinstance(value, ItemCheckedValue):
            self._itemStatus = value
        else:
            self._itemStatus = ItemCheckedValue.CHECKED if value else ItemCheckedValue.UNCHECKED
        self.dirty_fields.add("itemStatus")
    
    @property
    def version(self) -> "int | None":
        return self._version

    @property
    def quantity(self) -> "int | None":
        return self._quantity

    @quantity.setter
    def quantity(self, value: "int | None"):
        v = int(value) if value is not None else None
        self._quantity = min(v, 999) if v and v > 1 else None
        self.dirty_fields.add("quantity")

    @property
    def note(self) -> "str | None":
        return self._note

    @note.setter
    def note(self, value: "str | None"):
        self._note = value[:256] if value else value
        self.dirty_fields.add("note")

    @property
    def dirty(self) -> bool:
        return self._itemId is None or bool(self.dirty_fields) or self.deleted
    
    def clean(self) -> None:
        """Clear dirty flags and snapshot current field values as the last-known server baseline."""
        self.dirty_fields.clear()
        self._deleted = None
        self._server_itemName = self._itemName
        self._server_itemStatus = self._itemStatus
        self._server_quantity = self._quantity
        self._server_note = self._note

    def __str__(self) -> str:
        text = f"{self.itemName} x{self.quantity}" if self.quantity and self.quantity > 1 else self.itemName
        return "{} {}".format(
            "☑" if self.checked else "☐",
            text,
        )
    #TODO NOTES?

    def __repr__(self) -> str:
        return (
            f"ListItem(itemName={self._itemName!r}, quantity={self._quantity!r}, "
            f"checked={self._itemStatus!r}, itemId={self._itemId!r}, id={self.id!r})"
        )
    #TODO NOTES?

class List(Resource):
    """pyalexalist list — holds ListItem instances and exposes sorted/filtered views over server state."""

    _DEFAULT_LIST_TYPES = {"SHOP", "TODO"}

    __slots__ = (
        "_listName",
        "_listId",
        "_listType",
        "_listStatus",
        "_version",
        "_items",
        "_deleted",
        "_server_listName",
        "_server_listStatus",
    )

    def __init__(self, listName: str = "") -> None:
        super().__init__()
        self._listName = listName
        self._listId = None
        self._listType = None
        self._listStatus = None
        self._version = None
        self._items = {}
        self._deleted = None
        self._server_listName = None
        self._server_listStatus = None

    def delete(self) -> None:
        if not self.isCustom:
            raise DefaultListModificationException(self.listName, "delete")
        self._deleted = True

    def undelete(self) -> None:
        if not self.isCustom:
            raise DefaultListModificationException(self.listName, "delete")
        self._deleted = False

    @property
    def deleted(self) -> "bool | None":
        return self._deleted

    def load(self, raw_list: dict, clean: bool = True) -> None:
        """Populate fields from a raw Alexa API list dict.

        Args:
            raw_list: API response dict with keys listId, listName (or listType as a
                fallback name for default lists), listType, listStatus, version, etc.
            clean: If True (default), clear dirty flags after loading.
        """
        self._listName = raw_list.get("listName", raw_list["listType"])
        self.createdTime = raw_list["createAt"]
        self.updatedTime = raw_list["updateAt"]
        self._listId = raw_list["listId"]
        self._listType = raw_list["listType"]
        self._listStatus = raw_list["listStatus"]
        self._version = raw_list["version"]
        if clean:
            self.clean()

    def clean(self) -> None:
        """Clear dirty flags and snapshot current field values as the last-known server baseline."""
        self.dirty_fields.clear()
        self._deleted = None
        self._server_listName = self._listName
        self._server_listStatus = self._listStatus

    @property
    def listName(self):
        return self._listName

    @listName.setter
    def listName(self, value: str):
        if not self.isCustom:
            raise DefaultListModificationException(self.listName, "rename")
        self._listName = value
        self.dirty_fields.add("listName")

    @property
    def version(self):
        return self._version

    @property
    def listId(self) -> "str | None":
        return self._listId

    @property
    def listType(self) -> "str | None":
        return self._listType

    @property
    def isCustom(self) -> bool:
        """True for user-created lists; False for Alexa's built-in SHOP/TODO lists."""
        return self._listType not in self._DEFAULT_LIST_TYPES

    @property
    def archived(self) -> bool:
        return self._listStatus == "ARCHIVED"

    @archived.setter
    def archived(self, value: bool):
        if not self.isCustom:
            raise DefaultListModificationException(self.listName, "archive" if value else "unarchive")
        if value:
            self._listStatus = "ARCHIVED"
        else:
            self._listStatus = "ACTIVE"
        self.dirty_fields.add("listStatus")

    def get(self, name: str | None = None, *, id: str | None = None, item_id: str | None = None) -> "ListItem | None":
        """Look up a single item by name, internal UUID, or server item_id.

        Args:
            name: Match by itemName (case-sensitive).
            id: Match by internal UUID (keyword-only).
            item_id: Match by server-assigned itemId (keyword-only).
        Returns:
            The first matching ListItem, or None.
        """
        if id is not None:
            return self._items.get(id)
        if item_id is not None:
            return next((i for i in self._items.values() if i.itemId == item_id), None)
        if name is not None:
            return next((i for i in self._items.values() if i.itemName == name), None)
        return None

    def add(self, text: str, checked: bool = False, quantity: int | None = None, note: str | None = None) -> ListItem:
        """Create and add a new ListItem to this list.

        Args:
            text: Item display name.
            checked: Initial checked state; defaults to False.
            quantity: Optional quantity.
            note: Optional item note.
        Returns:
            The newly created ListItem.
        """
        item = ListItem(itemName=text, checked=checked, quantity=quantity, note=note)
        self._items[item.id] = item
        return item

    def update(self, new_item: ListItem) -> None:
        """Insert or replace an item, keyed by its id.
        """
        self._items[new_item.id] = new_item

    def remove(self, new_item: ListItem) -> None:
        self._items.pop(new_item.id, None)

    @property
    def items(self) -> list[ListItem]:
        """Get all items, including ones locally marked for deletion but not yet pushed.

        Used internally by push()/pull()/dirty, which need to see pending deletions.
        Use checkedItems/uncheckedItems for a display-oriented, deletion-filtered view.
        """
        return list(self._items.values())

    def _visible_items(self, checked: "bool | None" = None) -> list[ListItem]:
        """Return items excluding ones locally marked for deletion, optionally filtered by checked state."""
        return [
            item for item in self._items.values()
            if not item.deleted and (checked is None or bool(item.checked) == checked)
        ]

    @property
    def checkedItems(self) -> list[ListItem]:
        return self._visible_items(True)

    @property
    def uncheckedItems(self) -> list[ListItem]:
        return self._visible_items(False)

    @property
    def supportsQuantity(self) -> bool:
        """False for Alexa to-do lists — they have no quantity field."""
        return self.listName.casefold() != "todo"

    @property
    def dirty(self) -> bool:
        return bool(self.dirty_fields) or self.deleted or any(item.dirty for item in self.items)

    @property
    def text(self) -> str:
        return "\n".join(str(i) for i in self._visible_items())

    def sort_items(self, key: Callable = attrgetter("updatedTime"), reverse: bool = True) -> None:
        """Reorder items in place. Local view only, so it is NOT pushed to the server
        and will be overwritten by the next pull()/resync().

        Args:
            key: Sort key callable; defaults to updatedTime.
            reverse: Descending order when True.
        """
        sorted_list = sorted(self._items.values(), key=key, reverse=reverse)
        self._items = {item.id: item for item in sorted_list}

    def __str__(self) -> str:
        return "\n".join([self.listName] + [str(i) for i in self._visible_items()])
    
