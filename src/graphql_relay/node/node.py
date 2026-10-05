from base64 import b64decode
import binascii
from typing import Any, Callable, Dict, NamedTuple, Optional, Union

from graphql_relay.utils.base64 import base64, unbase64

from graphql import (
    GraphQLError,
    GraphQLArgument,
    GraphQLNonNull,
    GraphQLID,
    GraphQLField,
    GraphQLInterfaceType,
    GraphQLList,
    GraphQLResolveInfo,
    GraphQLScalarType,
    GraphQLTypeResolver,
)
from graphql.language import StringValueNode, print_ast

__all__ = [
    "from_global_id",
    "global_id_field",
    "global_id_scalar",
    "node_definitions",
    "to_global_id",
    "GraphQLNodeDefinitions",
    "ResolvedGlobalId",
]


class GraphQLNodeDefinitions(NamedTuple):

    node_interface: GraphQLInterfaceType
    node_field: GraphQLField
    nodes_field: GraphQLField


def node_definitions(
    fetch_by_id: Callable[[str, GraphQLResolveInfo], Any],
    type_resolver: Optional[GraphQLTypeResolver] = None,
) -> GraphQLNodeDefinitions:
    """
    Given a function to map from an ID to an underlying object, and a function
    to map from an underlying object to the concrete GraphQLObjectType it
    corresponds to, constructs a `Node` interface that objects can implement,
    and a field object to be used as a `node` root field.

    If the type_resolver is omitted, object resolution on the interface will be
    handled with the `is_type_of` method on object types, as with any GraphQL
    interface without a provided `resolve_type` method.
    """
    node_interface = GraphQLInterfaceType(
        "Node",
        description="An object with an ID",
        fields=lambda: {
            "id": GraphQLField(
                GraphQLNonNull(GraphQLID), description="The id of the object."
            )
        },
        resolve_type=type_resolver,
    )

    # noinspection PyShadowingBuiltins
    node_field = GraphQLField(
        node_interface,
        description="Fetches an object given its ID",
        args={
            "id": GraphQLArgument(
                GraphQLNonNull(GraphQLID), description="The ID of an object"
            )
        },
        resolve=lambda _obj, info, id: fetch_by_id(id, info),
    )

    nodes_field = GraphQLField(
        GraphQLNonNull(GraphQLList(node_interface)),
        description="Fetches objects given their IDs",
        args={
            "ids": GraphQLArgument(
                GraphQLNonNull(GraphQLList(GraphQLNonNull(GraphQLID))),
                description="The IDs of objects",
            )
        },
        resolve=lambda _obj, info, ids: [fetch_by_id(id_, info) for id_ in ids],
    )

    return GraphQLNodeDefinitions(node_interface, node_field, nodes_field)


class ResolvedGlobalId(NamedTuple):

    type: str
    id: str


def to_global_id(type_: str, id_: Union[str, int]) -> str:
    """
    Takes a type name and an ID specific to that type name, and returns a
    "global ID" that is unique among all types.
    """
    return base64(f"{type_}:{GraphQLID.serialize(id_)}")


def from_global_id(global_id: str) -> ResolvedGlobalId:
    """
    Takes the "global ID" created by to_global_id, and returns the type name and ID
    used to create it.
    """
    global_id = unbase64(global_id)
    if ":" not in global_id:
        return ResolvedGlobalId("", global_id)
    return ResolvedGlobalId(*global_id.split(":", 1))


def global_id_scalar(
    *allowed_types: str,
    aliases: Optional[Dict[str, str]] = None,
    name: str = "GlobalId",
    description: Optional[str] = None,
) -> GraphQLScalarType:
    """
    Creates an input scalar type that accepts Relay global IDs for a declared
    set of node types only.

    The scalar decodes global IDs while the request is being coerced, so
    malformed IDs (invalid Base64, missing or empty type) and IDs of types
    that are not allowed are rejected before any resolver runs. Resolvers
    receive a structured `ResolvedGlobalId` (with `type` and `id` attributes)
    instead of an unchecked string.

    When several public type names have been migrated to the same internal
    type, the public names can be mapped with `aliases`, e.g.
    `aliases={"Customer": "User", "Account": "User"}` decodes IDs of the
    public types "Customer" and "Account" to the internal type "User".
    Alias configurations that conflict with the allowed types are rejected
    immediately, i.e. while the schema is being built.

    Note that default values for arguments or input fields of this type
    should be given as already decoded `ResolvedGlobalId` values, since
    schema-side default values are not parsed again.
    """
    if not allowed_types:
        raise TypeError("global_id_scalar() requires at least one allowed type")
    for type_name in allowed_types:
        if not isinstance(type_name, str) or not type_name:
            raise TypeError("allowed types must be non-empty strings")
    allowed = set(allowed_types)
    if len(allowed) != len(allowed_types):
        raise ValueError(
            f"Conflicting configuration for scalar '{name}':"
            " duplicate allowed types."
        )

    alias_map = dict(aliases) if aliases else {}
    for public_type, internal_type in alias_map.items():
        if not isinstance(public_type, str) or not public_type:
            raise TypeError("alias types must be non-empty strings")
        if public_type in allowed:
            raise ValueError(
                f"Conflicting configuration for scalar '{name}':"
                f" '{public_type}' is both an allowed type and an alias"
                f" for '{internal_type}'."
            )
        if internal_type not in allowed:
            raise ValueError(
                f"Conflicting configuration for scalar '{name}':"
                f" alias '{public_type}' maps to '{internal_type}'"
                " which is not an allowed type."
            )

    expected = ", ".join(sorted(allowed | set(alias_map)))

    def decode_global_id(value: str) -> ResolvedGlobalId:
        try:
            decoded = b64decode(value.encode("ascii"), validate=True).decode("utf-8")
        except (UnicodeEncodeError, binascii.Error, UnicodeDecodeError):
            raise GraphQLError(
                f"Invalid global ID {value!r}: not a valid Base64 string."
            )
        type_, separator, id_ = decoded.partition(":")
        if not separator:
            raise GraphQLError(
                f"Invalid global ID {value!r}: expected the format '<type>:<id>'."
            )
        if not type_:
            raise GraphQLError(f"Invalid global ID {value!r}: empty type.")
        internal_type = alias_map.get(type_, type_)
        if internal_type not in allowed:
            raise GraphQLError(
                f"Unknown type {type_!r} in global ID {value!r}:"
                f" expected one of: {expected}."
            )
        return ResolvedGlobalId(internal_type, id_)

    def parse_value(value: Any) -> ResolvedGlobalId:
        if isinstance(value, ResolvedGlobalId):
            return value
        if not isinstance(value, str):
            raise GraphQLError(
                f"Global ID cannot represent a non-string value: {value!r}."
            )
        return decode_global_id(value)

    def parse_literal(node: Any, _variables: Any = None) -> ResolvedGlobalId:
        if not isinstance(node, StringValueNode):
            raise GraphQLError(
                f"Global ID cannot represent a non-string literal: {print_ast(node)}.",
                node,
            )
        return decode_global_id(node.value)

    def serialize(value: Any) -> str:
        if isinstance(value, ResolvedGlobalId):
            return to_global_id(value.type, value.id)
        if isinstance(value, str):
            return value
        raise GraphQLError(f"Global ID cannot represent value: {value!r}.")

    if description is None:
        description = (
            "A Relay global ID restricted to the types: " + expected + "."
        )

    return GraphQLScalarType(
        name=name,
        description=description,
        serialize=serialize,
        parse_value=parse_value,
        parse_literal=parse_literal,
    )


def global_id_field(
    type_name: Optional[str] = None,
    id_fetcher: Optional[Callable[[Any, GraphQLResolveInfo], str]] = None,
) -> GraphQLField:
    """
    Creates the configuration for an id field on a node, using `to_global_id` to
    construct the ID from the provided typename. The type-specific ID is fetched
    by calling id_fetcher on the object, or if not provided, by accessing the `id`
    attribute of the object, or the `id` if the object is a dict.
    """

    def resolve(obj: Any, info: GraphQLResolveInfo, **_args: Any) -> str:
        type_ = type_name or info.parent_type.name
        id_ = (
            id_fetcher(obj, info)
            if id_fetcher
            else (obj["id"] if isinstance(obj, dict) else obj.id)
        )
        return to_global_id(type_, id_)

    return GraphQLField(
        GraphQLNonNull(GraphQLID), description="The ID of an object", resolve=resolve
    )
