
import uuid
import datetime
import enum
from collections.abc import Callable
from operator import attrgetter


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

    def __init__(self) -> None:
        self._id = self.generateId()
        self._createdTime = None
        self._updatedTime = None
        self._dirty_fields: set[str] = set()

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

    def __init__(self, itemName: str = "", checked: bool = False, quantity: int | None = None, note: str | None = None) -> None:
        super().__init__()
        self._itemId = None
        self._itemName = itemName
        self._itemStatus = ItemCheckedValue.CHECKED if checked else ItemCheckedValue.UNCHECKED
        self._quantity = quantity #TODO what is max???
        self._note = note
        self._version = None
        self._deleted = None
        self._server_itemName = None
        self._server_itemStatus = None
        self._server_quantity = None

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
        self.quantity = raw_item.get("quantity")
        if clean:
            self.clean()

    def delete(self) -> None:
        self._deleted = True

    def undelete(self) -> None:
        self._deleted = False
    
    @property
    def deleted(self) -> "bool | None":
        return self._deleted
    
    @deleted.setter
    def deleted(self, value: "bool | None"):
        self._deleted = value

    @property
    def itemId(self) -> "str | None":
        return self._itemId

    @property
    def itemName(self) -> str:
        return self._itemName

    @itemName.setter
    def itemName(self, value: str):
        self._itemName = value.strip()
        self._dirty_fields.add("itemName")

    @property
    def checked(self) -> "ItemCheckedValue":
        return self._itemStatus

    @checked.setter
    def checked(self, value: "ItemCheckedValue | bool"):
        if isinstance(value, ItemCheckedValue):
            self._itemStatus = value
        else:
            self._itemStatus = ItemCheckedValue.CHECKED if value else ItemCheckedValue.UNCHECKED
        self._dirty_fields.add("itemStatus")
    
    @property
    def version(self) -> "int | None":
        return self._version

    @property
    def quantity(self) -> "int | None":
        return self._quantity

    @quantity.setter
    def quantity(self, value: "int | None"):
        v = int(value) if value is not None else None
        self._quantity = v if v and v > 1 else None
        self._dirty_fields.add("quantity")

    @property
    def dirty(self) -> bool:
        return self._itemId is None or bool(self._dirty_fields) or self.deleted
    
    def clean(self) -> None:
        """Clear dirty flags and snapshot current field values as the last-known server baseline."""
        self._dirty_fields.clear()
        self._deleted = None
        self._server_itemName = self._itemName
        self._server_itemStatus = self._itemStatus
        self._server_quantity = self._quantity

    def __str__(self) -> str:
        return self._itemName

    def __repr__(self) -> str:
        return (
            f"ListItem(itemName={self._itemName!r}, quantity={self._quantity!r}, "
            f"checked={self._itemStatus!r}, itemId={self._itemId!r}, id={self.id!r})"
        )


#TODO qty? note?? check box??
#    def __str__(self) -> str:
#         return "{}{} {}".format(
#             "  " if self.indented else "",
#             "☑" if self.checked else "☐",
#             self.text,
#         )

#     def __str__(self) -> str:
#         return "\n".join([self.title] + [str(node) for node in self.items])

class List(Resource):
    """pyalexalist list — holds ListItem instances and exposes sorted/filtered views over server state."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        self.listId = None
        self._items = {}
        #TODO add docstrings
        #archive
        #default lists??
        #custom ones??

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

#TODO need to check if these are good getters?????
# def sort_items(
    def update(self, new_item: ListItem) -> None:
        self._items[new_item.id] = new_item
        #TODO better approach?? item_id???

    def remove(self, new_item: ListItem) -> None:
        del self._items[new_item.id]

    @property
    def items(self) -> list[ListItem]:
        return list(self._items.values())

    @property
    def namesOfUncheckedItems(self) -> list[str]:
        return sorted(item.itemName for item in self._items.values() if not item.checked)

    @property
    def namesOfCheckedItems(self) -> list[str]:
        return sorted(item.itemName for item in self._items.values() if item.checked)

    @property
    def idsOfItems(self) -> list[str]:
        return [item.id for item in self._items.values()]

    @property
    def serverIdsOfItems(self) -> list[str | None]:
        return [item.itemId for item in self._items.values()]

    @property
    def supportsQuantity(self) -> bool:
        """False for Alexa to-do lists — they have no quantity field."""
        return self.name.casefold() != "todo"
    
    @property
    def dirty(self) -> bool:
        return bool(self._dirty_fields) or any(item.dirty for item in self.items)
    
    # checked??

    def sort_items(self, key: Callable = attrgetter("updatedTime"), reverse: bool = True) -> None:
        """Sort items in place. Defaults to newest first, matching Alexa's default order.

        Args:
            key: Sort key callable; defaults to updatedTime.
            reverse: Descending order when True.
        """
        sorted_list = sorted(self._items.values(), key=key, reverse=reverse)
        self._items = {item.id: item for item in sorted_list}

    def sorted_items(self, key: Callable = attrgetter("updatedTime"), reverse: bool = True) -> list["ListItem"]:
        """Return a sorted copy without mutating the list. Defaults to newest first.

        Args:
            key: Sort key callable; defaults to updatedTime.
            reverse: Descending order when True.
        Returns:
            Sorted list of ListItem objects.
        """
        return sorted(self._items.values(), key=key, reverse=reverse)

 
