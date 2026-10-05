import asyncio
from dataclasses import dataclass
from types import MappingProxyType

from pytest import mark

from graphql import (
    graphql,
    graphql_sync,
    print_schema,
    print_type,
    GraphQLField,
    GraphQLFieldMap,
    GraphQLInputField,
    GraphQLInt,
    GraphQLObjectType,
    GraphQLSchema,
)

from graphql_relay import mutation_with_client_mutation_id

from ..utils import dedent


class Result:

    # noinspection PyPep8Naming
    def __init__(self, result, clientMutationId=None):
        self.clientMutationId = clientMutationId
        self.result = result


def dummy_resolve(_info, inputData=None, clientMutationId=None):
    return Result(inputData or 1, clientMutationId)


async def dummy_resolve_async(_info, inputData=None, clientMutationId=None):
    return Result(inputData or 1, clientMutationId)


def wrap_in_schema(mutation_fields: GraphQLFieldMap) -> GraphQLSchema:
    query_type = GraphQLObjectType("Query", fields={"dummy": GraphQLField(GraphQLInt)})
    mutation_type = GraphQLObjectType("Mutation", fields=mutation_fields)
    return GraphQLSchema(query_type, mutation_type)


def describe_mutation_with_client_mutation_id():
    def requires_an_argument():
        some_mutation = mutation_with_client_mutation_id(
            "SomeMutation", {}, {"result": GraphQLField(GraphQLInt)}, dummy_resolve
        )
        schema = wrap_in_schema({"someMutation": some_mutation})
        source = """
            mutation {
              someMutation {
                result
              }
            }
            """
        assert graphql_sync(schema, source) == (
            None,
            [
                {
                    "message": "Field 'someMutation' argument 'input'"
                    " of type 'SomeMutationInput!' is required,"
                    " but it was not provided.",
                    "locations": [(3, 15)],
                }
            ],
        )

    def returns_the_same_client_mutation_id():
        some_mutation = mutation_with_client_mutation_id(
            "SomeMutation", {}, {"result": GraphQLField(GraphQLInt)}, dummy_resolve
        )
        schema = wrap_in_schema({"someMutation": some_mutation})
        source = """
            mutation {
              someMutation(input: {clientMutationId: "abc"}) {
                result
                clientMutationId
              }
            }
            """
        assert graphql_sync(schema, source) == (
            {"someMutation": {"result": 1, "clientMutationId": "abc"}},
            None,
        )

    def supports_thunks_as_input_and_output_fields():
        some_mutation = mutation_with_client_mutation_id(
            "SomeMutation",
            {"inputData": GraphQLInputField(GraphQLInt)},
            {"result": GraphQLField(GraphQLInt)},
            dummy_resolve,
        )
        schema = wrap_in_schema({"someMutation": some_mutation})
        source = """
            mutation {
              someMutation(input: {inputData: 1234, clientMutationId: "abc"}) {
                result
                clientMutationId
              }
            }
            """
        assert graphql_sync(schema, source) == (
            {
                "someMutation": {
                    "result": 1234,
                    "clientMutationId": "abc",
                }
            },
            None,
        )

    @mark.asyncio
    async def supports_async_mutations():
        some_mutation = mutation_with_client_mutation_id(
            "SomeMutation",
            {},
            {"result": GraphQLField(GraphQLInt)},
            dummy_resolve_async,
        )
        schema = wrap_in_schema({"someMutation": some_mutation})
        source = """
            mutation {
              someMutation(input: {clientMutationId: "abc"}) {
                result
                clientMutationId
              }
            }
            """
        assert await graphql(schema, source) == (
            {"someMutation": {"result": 1, "clientMutationId": "abc"}},
            None,
        )

    def can_access_root_value():
        some_mutation = mutation_with_client_mutation_id(  # pragma: no cover
            "SomeMutation",
            {},
            {"result": GraphQLField(GraphQLInt)},
            lambda info, clientMutationId=None: Result(
                info.root_value, clientMutationId
            ),
        )

        wrapper_type = GraphQLObjectType("WrapperType", {"someMutation": some_mutation})
        assert print_type(wrapper_type) == dedent(
            """
            type WrapperType {
              someMutation(input: SomeMutationInput!): SomeMutationPayload
            }
            """
        )

    def supports_mutations_returning_null():
        def null_resolve(_info, **_input):
            return None

        some_mutation = mutation_with_client_mutation_id(
            "SomeMutation", {}, {"result": GraphQLField(GraphQLInt)}, null_resolve
        )
        schema = wrap_in_schema({"someMutation": some_mutation})
        source = """
            mutation {
              someMutation(input: {clientMutationId: "abc"}) {
                result
                clientMutationId
              }
            }
            """
        assert graphql_sync(schema, source) == (
            {"someMutation": {"result": None, "clientMutationId": "abc"}},
            None,
        )

    @mark.asyncio
    async def supports_async_mutations_returning_null():
        async def null_resolve(_info, **_input):
            return None

        some_mutation = mutation_with_client_mutation_id(
            "SomeMutation",
            {},
            {"result": GraphQLField(GraphQLInt)},
            null_resolve,
        )
        schema = wrap_in_schema({"someMutation": some_mutation})
        source = """
            mutation {
              someMutation(input: {clientMutationId: "abc"}) {
                result
                clientMutationId
              }
            }
            """
        assert await graphql(schema, source) == (
            {"someMutation": {"result": None, "clientMutationId": "abc"}},
            None,
        )

    def supports_mutations_returning_custom_classes():
        class SomeClass:
            @staticmethod
            def get_some_generated_data():
                return 1

            @classmethod
            def mutate(cls, _info, **_input):
                return cls()

            @classmethod
            def resolve(cls, obj, _info):
                assert isinstance(obj, cls)
                return obj.get_some_generated_data()

        some_mutation = mutation_with_client_mutation_id(
            "SomeMutation",
            {},
            {"result": GraphQLField(GraphQLInt, resolve=SomeClass.resolve)},
            SomeClass.mutate,
        )
        schema = wrap_in_schema({"someMutation": some_mutation})
        source = """
            mutation {
              someMutation(input: {clientMutationId: "abc"}) {
                result
                clientMutationId
              }
            }
            """
        assert graphql_sync(schema, source) == (
            {"someMutation": {"result": 1, "clientMutationId": "abc"}},
            None,
        )

    def supports_mutations_returning_mappings():
        def dict_mutate(_info, **_input):
            return {"some_data": 1}

        def dict_resolve(obj, _info):
            return obj["some_data"]

        some_mutation = mutation_with_client_mutation_id(
            "SomeMutation",
            {},
            {"result": GraphQLField(GraphQLInt, resolve=dict_resolve)},
            dict_mutate,
        )
        schema = wrap_in_schema({"someMutation": some_mutation})
        source = """
            mutation {
              someMutation(input: {clientMutationId: "abc"}) {
                result
                clientMutationId
              }
            }
            """
        assert graphql_sync(schema, source) == (
            {"someMutation": {"result": 1, "clientMutationId": "abc"}},
            None,
        )

    @mark.asyncio
    async def supports_async_mutations_returning_mappings():
        async def dict_mutate(_info, **_input):
            return {"some_data": 1}

        async def dict_resolve(obj, _info):
            return obj["some_data"]

        some_mutation = mutation_with_client_mutation_id(
            "SomeMutation",
            {},
            {"result": GraphQLField(GraphQLInt, resolve=dict_resolve)},
            dict_mutate,
        )
        schema = wrap_in_schema({"someMutation": some_mutation})
        source = """
            mutation {
              someMutation(input: {clientMutationId: "abc"}) {
                result
                clientMutationId
              }
            }
            """
        assert await graphql(schema, source) == (
            {"someMutation": {"result": 1, "clientMutationId": "abc"}},
            None,
        )

    def generates_correct_types():
        some_mutation = mutation_with_client_mutation_id(
            "SomeMutation",
            description="Some Mutation Description",
            input_fields={},
            output_fields={"result": GraphQLField(GraphQLInt)},
            mutate_and_get_payload=dummy_resolve,
            deprecation_reason="Just because",
        )

        schema = wrap_in_schema({"someMutation": some_mutation})

        assert print_schema(schema) == dedent(
            '''
            type Query {
              dummy: Int
            }

            type Mutation {
              """Some Mutation Description"""
              someMutation(input: SomeMutationInput!): SomeMutationPayload @deprecated(reason: "Just because")
            }

            type SomeMutationPayload {
              result: Int
              clientMutationId: String
            }

            input SomeMutationInput {
              clientMutationId: String
            }
            '''  # noqa: E501
        )

    def supports_mutations_returning_frozen_dataclasses():
        @dataclass(frozen=True)
        class FrozenResult:
            result: int

        payload = FrozenResult(42)

        def frozen_resolve(_info, **_input):
            return payload

        def read_attribute(obj, _info):
            # A custom resolver that actively reads an attribute of the
            # original immutable result.
            return obj.result

        some_mutation = mutation_with_client_mutation_id(
            "SomeMutation",
            {},
            {"result": GraphQLField(GraphQLInt, resolve=read_attribute)},
            frozen_resolve,
        )
        schema = wrap_in_schema({"someMutation": some_mutation})
        source = """
            mutation {
              someMutation(input: {clientMutationId: "abc"}) {
                result
                clientMutationId
              }
            }
            """
        assert graphql_sync(schema, source) == (
            {"someMutation": {"result": 42, "clientMutationId": "abc"}},
            None,
        )
        # The original payload is not modified.
        assert payload == FrozenResult(42)

    def supports_mutations_returning_read_only_mappings():
        payload = MappingProxyType({"result": 7})

        def mapping_resolve(_info, **_input):
            return payload

        def read_key(obj, _info):
            return obj["result"]

        some_mutation = mutation_with_client_mutation_id(
            "SomeMutation",
            {},
            {"result": GraphQLField(GraphQLInt, resolve=read_key)},
            mapping_resolve,
        )
        schema = wrap_in_schema({"someMutation": some_mutation})
        source = """
            mutation {
              someMutation(input: {clientMutationId: "abc"}) {
                result
                clientMutationId
              }
            }
            """
        assert graphql_sync(schema, source) == (
            {"someMutation": {"result": 7, "clientMutationId": "abc"}},
            None,
        )
        # The original mapping is not modified.
        assert dict(payload) == {"result": 7}

    @mark.asyncio
    async def allows_reusing_the_same_immutable_payload_between_calls():
        @dataclass(frozen=True)
        class SharedResult:
            result: int

        shared_payload = SharedResult(1)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def shared_resolve(_info, **_input):
            # Force two interleaved calls to suspend while they share the
            # very same immutable business result.
            if entered.is_set():
                release.set()
            else:
                entered.set()
                await release.wait()
            return shared_payload

        def read_attribute(obj, _info):
            return obj.result

        some_mutation = mutation_with_client_mutation_id(
            "SomeMutation",
            {},
            {"result": GraphQLField(GraphQLInt, resolve=read_attribute)},
            shared_resolve,
        )
        schema = wrap_in_schema({"someMutation": some_mutation})
        source = """
            mutation($id: String!) {
              someMutation(input: {clientMutationId: $id}) {
                result
                clientMutationId
              }
            }
            """

        async def execute_with(client_mutation_id):
            return await graphql(
                schema, source, variable_values={"id": client_mutation_id}
            )

        first, second = await asyncio.gather(
            execute_with("first"), execute_with("second")
        )
        results = [first.data, second.data]
        assert {"someMutation": {"result": 1, "clientMutationId": "first"}} in results
        assert {"someMutation": {"result": 1, "clientMutationId": "second"}} in results
        assert first.errors is None and second.errors is None
        # The shared payload is unchanged after both interleaved calls.
        assert shared_payload == SharedResult(1)

    @mark.asyncio
    async def awaits_awaitables_returned_by_synchronous_functions():
        @dataclass(frozen=True)
        class FrozenResult:
            result: int

        payload = FrozenResult(99)

        def sync_function(_info, **_input):
            # Declared with ``def``, but actually returns an awaitable.
            async def awaitable_result():
                return payload

            return awaitable_result()

        some_mutation = mutation_with_client_mutation_id(
            "SomeMutation",
            {},
            {"result": GraphQLField(GraphQLInt)},
            sync_function,
        )
        schema = wrap_in_schema({"someMutation": some_mutation})
        source = """
            mutation {
              someMutation(input: {clientMutationId: "abc"}) {
                result
                clientMutationId
              }
            }
            """
        assert await graphql(schema, source) == (
            {"someMutation": {"result": 99, "clientMutationId": "abc"}},
            None,
        )
        assert payload == FrozenResult(99)
