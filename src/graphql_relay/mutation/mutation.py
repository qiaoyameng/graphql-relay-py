from collections.abc import Mapping
from inspect import isawaitable, iscoroutinefunction
from typing import Any, Callable, Dict, Optional

from graphql import (
    resolve_thunk,
    GraphQLArgument,
    GraphQLField,
    GraphQLFieldMap,
    GraphQLInputField,
    GraphQLInputFieldMap,
    GraphQLInputObjectType,
    GraphQLNonNull,
    GraphQLObjectType,
    GraphQLResolveInfo,
    GraphQLString,
    ThunkMapping,
)
from graphql.pyutils import AwaitableOrValue

__all__ = [
    "mutation_with_client_mutation_id",
    "MutationFn",
    "MutationFnWithoutArgs",
    "NullResult",
]

# Note: Contrary to the Javascript implementation of MutationFn,
# the context is passed as part of the GraphQLResolveInfo and any arguments
# are passed individually as keyword arguments.
MutationFnWithoutArgs = Callable[[GraphQLResolveInfo], AwaitableOrValue[Any]]
# Unfortunately there is currently no syntax to indicate optional or keyword
# arguments in Python, so we also allow any other Callable as a workaround:
MutationFn = Callable[..., AwaitableOrValue[Any]]


class NullResult:
    def __init__(self, clientMutationId: Optional[str] = None) -> None:
        self.clientMutationId = clientMutationId


class ObjectResult:
    """Read-only wrapper around an immutable object payload.

    Holds the ``clientMutationId`` of the current call separately, without
    mutating the original object.  Every other attribute, method or property is
    delegated to the wrapped object, so custom output field resolvers keep
    working and the same payload can be reused across different requests.
    """

    def __init__(self, payload: Any, clientMutationId: Optional[str]) -> None:
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "clientMutationId", clientMutationId)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.payload, name)


class MappingResult(Mapping):
    """Read-only wrapper around an immutable mapping payload.

    Behaves like a read-only mapping backed by the original payload, with the
    ``clientMutationId`` of the current call layered on top.  The original
    mapping is never modified and may be shared by multiple requests.
    """

    def __init__(self, payload: Mapping, clientMutationId: Optional[str]) -> None:
        self._payload = payload
        self._client_mutation_id = clientMutationId

    def __getitem__(self, name: str) -> Any:
        if name == "clientMutationId":
            return self._client_mutation_id
        return self._payload[name]

    def __iter__(self):
        yield from self._payload
        if "clientMutationId" not in self._payload:
            yield "clientMutationId"

    def __len__(self) -> int:
        return len(self._payload) + ("clientMutationId" not in self._payload)

    def get(self, name: str, default: Any = None) -> Any:
        if name == "clientMutationId":
            return self._client_mutation_id
        return self._payload.get(name, default)


def augment_payload(payload: Any, clientMutationId: Optional[str]) -> Any:
    """Attach the ``clientMutationId`` of the current call to the payload.

    Mutable payloads are updated directly, preserving their type and the
    previous behavior.  Payloads that cannot be modified (frozen dataclasses,
    read-only mappings and the like) are wrapped instead, leaving the original
    result untouched so it can be shared by several calls.
    """
    if payload is None:
        return NullResult(clientMutationId)
    if isinstance(payload, Mapping):
        try:
            payload["clientMutationId"] = clientMutationId
        except (TypeError, KeyError):
            return MappingResult(payload, clientMutationId)
        return payload
    try:
        payload.clientMutationId = clientMutationId
    except (AttributeError, TypeError):
        return ObjectResult(payload, clientMutationId)
    return payload


def mutation_with_client_mutation_id(
    name: str,
    input_fields: ThunkMapping[GraphQLInputField],
    output_fields: ThunkMapping[GraphQLField],
    mutate_and_get_payload: MutationFn,
    description: Optional[str] = None,
    deprecation_reason: Optional[str] = None,
    extensions: Optional[Dict[str, Any]] = None,
) -> GraphQLField:
    """
    Returns a GraphQLFieldConfig for the specified mutation.

    The input_fields and output_fields should not include `clientMutationId`,
    as this will be provided automatically.

    An input object will be created containing the input fields, and an
    object will be created containing the output fields.

    mutate_and_get_payload will receive a GraphQLResolveInfo as first argument,
    and the input fields as keyword arguments, and it should return an object
    (or a dict) with an attribute (or a key) for each output field.
    It may return synchronously or asynchronously, and a synchronous
    function may also return an awaitable.

    Immutable results (such as frozen dataclasses or read-only mappings)
    are supported as well: they are wrapped instead of being modified, so
    the very same result can be reused by different calls, each echoing
    back its own clientMutationId.
    """

    def augmented_input_fields() -> GraphQLInputFieldMap:
        return dict(
            resolve_thunk(input_fields),
            clientMutationId=GraphQLInputField(GraphQLString),
        )

    def augmented_output_fields() -> GraphQLFieldMap:
        return dict(
            resolve_thunk(output_fields),
            clientMutationId=GraphQLField(GraphQLString),
        )

    output_type = GraphQLObjectType(name + "Payload", fields=augmented_output_fields)

    input_type = GraphQLInputObjectType(name + "Input", fields=augmented_input_fields)

    # noinspection PyShadowingBuiltins
    def resolve(_root: Any, info: GraphQLResolveInfo, input: Dict) -> Any:
        payload = mutate_and_get_payload(info, **input)
        if isawaitable(payload):
            # A function declared with ``def`` may also return an awaitable.
            # The async execution path detects this and awaits the result.
            async def resolve_awaitable() -> Any:
                return augment_payload(await payload, input.get("clientMutationId"))

            return resolve_awaitable()
        return augment_payload(payload, input.get("clientMutationId"))

    if iscoroutinefunction(mutate_and_get_payload):

        # noinspection PyShadowingBuiltins
        async def async_resolve(
            _root: Any, info: GraphQLResolveInfo, input: Dict
        ) -> Any:
            payload = await mutate_and_get_payload(info, **input)
            return augment_payload(payload, input.get("clientMutationId"))

        field_resolve = async_resolve
    else:
        field_resolve = resolve

    return GraphQLField(
        output_type,
        description=description,
        deprecation_reason=deprecation_reason,
        args={"input": GraphQLArgument(GraphQLNonNull(input_type))},
        resolve=field_resolve,
        extensions=extensions,
    )
