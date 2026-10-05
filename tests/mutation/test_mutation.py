from asyncio import Event, gather
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

            def doubled_result(self):
                return self.result * 2

        payload = FrozenResult(2)

        def frozen_mutate(_info, **_input):
            return payload

        def doubled_resolve(obj, _info):
            # actively reads an attribute of the original result object
            return obj.doubled_result()

        some_mutation = mutation_with_client_mutation_id(
            "SomeMutation",
            {},
            {
                "result": GraphQLField(GraphQLInt),
                "doubled": GraphQLField(GraphQLInt, resolve=doubled_resolve),
            },
            frozen_mutate,
        )
        schema = wrap_in_schema({"someMutation": some_mutation})
        source = """
            mutation {
              someMutation(input: {clientMutationId: "abc"}) {
                result
                doubled
                clientMutationId
              }
            }
            """
        assert graphql_sync(schema, source) == (
            {
                "someMutation": {
                    "result": 2,
                    "doubled": 4,
                    "clientMutationId": "abc",
                }
            },
            None,
        )
        # the original immutable payload was not modified
        assert payload == FrozenResult(2)
        assert not hasattr(payload, "clientMutationId")

    def supports_mutations_returning_read_only_mappings():
        payload = MappingProxyType({"some_data": 1})

        def mapping_mutate(_info, **_input):
            return payload

        def mapping_resolve(obj, _info):
            return obj["some_data"]

        some_mutation = mutation_with_client_mutation_id(
            "SomeMutation",
            {},
            {"result": GraphQLField(GraphQLInt, resolve=mapping_resolve)},
            mapping_mutate,
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
        # the original read-only mapping was not modified
        assert dict(payload) == {"some_data": 1}

    @mark.asyncio
    async def supports_sync_mutations_returning_awaitables():
        async def get_payload():
            return Result(1)

        def awaitable_mutate(_info, **_input):
            # a sync function returning an awaitable
            return get_payload()

        some_mutation = mutation_with_client_mutation_id(
            "SomeMutation",
            {},
            {"result": GraphQLField(GraphQLInt)},
            awaitable_mutate,
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

    @mark.asyncio
    async def supports_interleaved_mutations_reusing_immutable_payload():
        @dataclass(frozen=True)
        class SharedResult:
            result: int

            def doubled_result(self):
                return self.result * 2

        payload = SharedResult(1)

        both_started = Event()
        started_count = 0

        async def shared_mutate(_info, **_input):
            nonlocal started_count
            started_count += 1
            if started_count == 2:
                both_started.set()
            # ensure both mutations are in flight before either returns
            # the same shared payload
            await both_started.wait()
            return payload

        def doubled_resolve(obj, _info):
            # actively reads an attribute of the original result object
            return obj.doubled_result()

        some_mutation = mutation_with_client_mutation_id(
            "SomeMutation",
            {},
            {
                "result": GraphQLField(GraphQLInt),
                "doubled": GraphQLField(GraphQLInt, resolve=doubled_resolve),
            },
            shared_mutate,
        )
        schema = wrap_in_schema({"someMutation": some_mutation})
        source_template = """
            mutation {
              someMutation(input: {clientMutationId: "%s"}) {
                result
                doubled
                clientMutationId
              }
            }
            """
        result_a, result_b = await gather(
            graphql(schema, source_template % "a"),
            graphql(schema, source_template % "b"),
        )
        assert result_a == (
            {
                "someMutation": {
                    "result": 1,
                    "doubled": 2,
                    "clientMutationId": "a",
                }
            },
            None,
        )
        assert result_b == (
            {
                "someMutation": {
                    "result": 1,
                    "doubled": 2,
                    "clientMutationId": "b",
                }
            },
            None,
        )
        # the shared immutable payload was not modified
        assert payload == SharedResult(1)
        assert not hasattr(payload, "clientMutationId")
