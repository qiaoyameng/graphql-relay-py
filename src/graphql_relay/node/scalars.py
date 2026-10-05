import binascii
from base64 import b64decode
from collections.abc import Collection as CollectionABC
from collections.abc import Mapping as MappingABC
from typing import Any, Collection, Mapping, Optional

from graphql import (
    GraphQLError,
    GraphQLID,
    GraphQLScalarType,
    StringValueNode,
    print_ast,
)

from graphql_relay.node.node import ResolvedGlobalId, to_global_id

__all__ = ["global_id_scalar", "node_id_scalar"]


def global_id_scalar(
    name: str,
    types: Collection[str],
    aliases: Optional[Mapping[str, str]] = None,
    description: Optional[str] = None,
) -> GraphQLScalarType:
    """Create a strict global ID input scalar.

    The accepted public node types are declared when the scalar is created.
    Aliases map additional public type names to their canonical internal type.
    """
    if isinstance(types, (str, bytes)) or not isinstance(types, CollectionABC):
        raise TypeError("Global ID scalar types must be a collection of strings.")

    type_mapping = {}
    for type_name in types:
        if not isinstance(type_name, str) or not type_name:
            raise TypeError("Global ID scalar types must be non-empty strings.")
        type_mapping[type_name] = type_name

    if not type_mapping:
        raise TypeError("A global ID scalar must allow at least one node type.")

    if aliases is not None:
        if not isinstance(aliases, MappingABC):
            raise TypeError("Global ID scalar aliases must be a mapping.")
        for public_type, internal_type in aliases.items():
            if not isinstance(public_type, str) or not public_type:
                raise TypeError("Global ID alias names must be non-empty strings.")
            if not isinstance(internal_type, str) or not internal_type:
                raise TypeError("Global ID alias targets must be non-empty strings.")
            if internal_type not in types:
                raise TypeError(
                    f"Global ID alias {public_type!r} references unknown node type"
                    f" {internal_type!r}."
                )
            current_type = type_mapping.get(public_type)
            if current_type is not None and current_type != internal_type:
                raise TypeError(
                    f"Global ID type {public_type!r} cannot be mapped to both"
                    f" {current_type!r} and {internal_type!r}."
                )
            type_mapping[public_type] = internal_type

    if description is None:
        allowed_types = ", ".join(type_mapping)
        description = f"A globally unique ID. Accepted node types: {allowed_types}."

    return _GlobalIDScalarType(name, type_mapping, description)


node_id_scalar = global_id_scalar


class _GlobalIDScalarType(GraphQLScalarType):
    def __init__(
        self,
        name: str,
        type_mapping: Mapping[str, str],
        description: str,
    ) -> None:
        self.global_id_type_mapping = dict(type_mapping)
        super().__init__(
            name,
            serialize=self._serialize,
            parse_value=self._parse_value,
            parse_literal=self._parse_literal,
            description=description,
        )

    def __hash__(self) -> int:
        return 0

    def __eq__(self, other: Any) -> bool:
        if self is other:
            return True
        if not isinstance(other, _GlobalIDScalarType):
            return NotImplemented

        for public_type, internal_type in self.global_id_type_mapping.items():
            other_type = other.global_id_type_mapping.get(public_type)
            if other_type is not None and other_type != internal_type:
                raise TypeError(
                    f"Global ID scalar {self.name!r} maps public node type"
                    f" {public_type!r} to {internal_type!r}, but scalar"
                    f" {other.name!r} maps it to {other_type!r}."
                )
        return False

    @staticmethod
    def _serialize(value: Any) -> str:
        if isinstance(value, ResolvedGlobalId):
            return to_global_id(value.type, value.id)
        return GraphQLID.serialize(value)

    def _parse_value(self, value: Any) -> ResolvedGlobalId:
        if not isinstance(value, str):
            raise GraphQLError("Global ID must be a string.")
        return self._parse_global_id(value)

    def _parse_global_id(self, value: str) -> ResolvedGlobalId:
        try:
            decoded_value = b64decode(value, validate=True).decode("utf-8")
        except (
            binascii.Error,
            UnicodeDecodeError,
            UnicodeEncodeError,
            ValueError,
        ) as error:
            raise GraphQLError(f"Invalid Base64 global ID: {value!r}.") from error

        if ":" not in decoded_value:
            raise GraphQLError(
                f"Global ID {value!r} must contain a node type and a local ID."
            )

        public_type, local_id = decoded_value.split(":", 1)
        if not public_type:
            raise GraphQLError(f"Global ID {value!r} must contain a node type.")

        internal_type = self.global_id_type_mapping.get(public_type)
        if internal_type is None:
            allowed_types = ", ".join(self.global_id_type_mapping)
            raise GraphQLError(
                f"Unknown global ID node type {public_type!r}."
                f" Allowed node types: {allowed_types}."
            )

        return ResolvedGlobalId(internal_type, local_id)

    def _parse_literal(self, node: Any, _variables: Any = None) -> ResolvedGlobalId:
        if not isinstance(node, StringValueNode):
            raise GraphQLError(
                f"Global ID cannot represent a non-string value: {print_ast(node)}.",
                node,
            )
        try:
            return self._parse_global_id(node.value)
        except GraphQLError as error:
            raise GraphQLError(
                error.message, node, original_error=error.original_error or error
            ) from error
