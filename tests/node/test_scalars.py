import base64

from pytest import raises

from graphql import (
    GraphQLError,
    GraphQLArgument,
    GraphQLField,
    GraphQLInputField,
    GraphQLInputObjectType,
    GraphQLList,
    GraphQLNonNull,
    GraphQLObjectType,
    GraphQLSchema,
    GraphQLString,
    graphql_sync,
)

from graphql_relay import ResolvedGlobalId, global_id_scalar, to_global_id


def base64_value(value):
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def make_schema(user_id_type, product_id_type):
    received_inputs = []

    nested_input_type = GraphQLInputObjectType(
        "NestedInput",
        {
            "userId": GraphQLInputField(GraphQLNonNull(user_id_type)),
            "productId": GraphQLInputField(GraphQLNonNull(product_id_type)),
            "productIds": GraphQLInputField(
                GraphQLList(GraphQLNonNull(product_id_type))
            ),
        },
    )

    def resolve(_root, _info, input):
        received_inputs.append(input)
        return "ok"

    query_type = GraphQLObjectType("Query", {"status": GraphQLField(GraphQLString)})
    mutation_type = GraphQLObjectType(
        "Mutation",
        {
            "change": GraphQLField(
                GraphQLString,
                args={"input": GraphQLArgument(GraphQLNonNull(nested_input_type))},
                resolve=resolve,
            )
        },
    )
    return GraphQLSchema(query=query_type, mutation=mutation_type), received_inputs


VARIABLE_MUTATION = """
mutation ($input: NestedInput!) {
  change(input: $input)
}
"""


def describe_global_id_scalar():
    def parses_ids_in_variables_nested_inputs_and_lists():
        user_id_type = global_id_scalar("UserID", ["User"])
        product_id_type = global_id_scalar("ProductID", ["Product"])
        schema, received_inputs = make_schema(user_id_type, product_id_type)

        result = graphql_sync(
            schema,
            VARIABLE_MUTATION,
            variable_values={
                "input": {
                    "userId": to_global_id("User", "42"),
                    "productId": to_global_id("Product", "7"),
                    "productIds": [
                        to_global_id("Product", "8"),
                        to_global_id("Product", "9"),
                    ],
                }
            },
        )

        assert result == ({"change": "ok"}, None)
        assert received_inputs == [
            {
                "userId": ResolvedGlobalId("User", "42"),
                "productId": ResolvedGlobalId("Product", "7"),
                "productIds": [
                    ResolvedGlobalId("Product", "8"),
                    ResolvedGlobalId("Product", "9"),
                ],
            }
        ]

    def leaves_absent_fields_absent_in_nested_inputs():
        user_id_type = global_id_scalar("UserID", ["User"])
        product_id_type = global_id_scalar("ProductID", ["Product"])
        schema, received_inputs = make_schema(user_id_type, product_id_type)

        result = graphql_sync(
            schema,
            VARIABLE_MUTATION,
            variable_values={
                "input": {
                    "userId": to_global_id("User", "42"),
                    "productId": to_global_id("Product", "7"),
                }
            },
        )

        assert result == ({"change": "ok"}, None)
        assert received_inputs == [
            {
                "userId": ResolvedGlobalId("User", "42"),
                "productId": ResolvedGlobalId("Product", "7"),
            }
        ]

    def reports_the_invalid_nested_item_for_variables_before_resolving():
        user_id_type = global_id_scalar("UserID", ["User"])
        product_id_type = global_id_scalar("ProductID", ["Product"])
        schema, received_inputs = make_schema(user_id_type, product_id_type)

        result = graphql_sync(
            schema,
            VARIABLE_MUTATION,
            variable_values={
                "input": {
                    "userId": to_global_id("User", "42"),
                    "productId": to_global_id("User", "7"),
                    "productIds": [to_global_id("Product", "8")],
                }
            },
        )

        assert result.errors is not None
        assert "at 'input.productId'" in result.errors[0].message
        assert "Unknown global ID node type 'User'" in result.errors[0].message
        assert received_inputs == []

    def reports_the_invalid_list_index_for_variables_before_resolving():
        user_id_type = global_id_scalar("UserID", ["User"])
        product_id_type = global_id_scalar("ProductID", ["Product"])
        schema, received_inputs = make_schema(user_id_type, product_id_type)

        result = graphql_sync(
            schema,
            VARIABLE_MUTATION,
            variable_values={
                "input": {
                    "userId": to_global_id("User", "42"),
                    "productId": to_global_id("Product", "7"),
                    "productIds": [
                        to_global_id("Product", "8"),
                        to_global_id("User", "9"),
                    ],
                }
            },
        )

        assert result.errors is not None
        assert "at 'input.productIds[1]'" in result.errors[0].message
        assert received_inputs == []

    def validates_inline_literal_ids_before_resolving():
        user_id_type = global_id_scalar("UserID", ["User"])
        product_id_type = global_id_scalar("ProductID", ["Product"])
        schema, received_inputs = make_schema(user_id_type, product_id_type)

        result = graphql_sync(
            schema,
            """
            mutation {
              change(input: {
                userId: "%s"
                productId: "not-base64"
              })
            }
            """
            % to_global_id("User", "42"),
        )

        assert result == (
            None,
            [
                {
                    "message": "Invalid Base64 global ID: 'not-base64'.",
                    "locations": [(5, 28)],
                }
            ],
        )
        assert received_inputs == []

    def parses_inline_literal_ids_into_structured_ids():
        user_id_type = global_id_scalar("UserID", ["User"])
        product_id_type = global_id_scalar("ProductID", ["Product"])
        schema, received_inputs = make_schema(user_id_type, product_id_type)

        result = graphql_sync(
            schema,
            """
            mutation {
              change(input: {
                userId: "%s"
                productId: "%s"
              })
            }
            """
            % (to_global_id("User", "42"), to_global_id("Product", "7")),
        )

        assert result == ({"change": "ok"}, None)
        assert received_inputs == [
            {
                "userId": ResolvedGlobalId("User", "42"),
                "productId": ResolvedGlobalId("Product", "7"),
            }
        ]

    def validates_empty_type_and_ids_without_a_type_separator():
        user_id_type = global_id_scalar("UserID", ["User"])
        schema, received_inputs = make_schema(user_id_type, user_id_type)

        empty_type_result = graphql_sync(
            schema,
            VARIABLE_MUTATION,
            variable_values={
                "input": {
                    "userId": base64_value(":42"),
                    "productId": to_global_id("User", "7"),
                }
            },
        )
        assert "must contain a node type" in empty_type_result.errors[0].message

        missing_separator_result = graphql_sync(
            schema,
            VARIABLE_MUTATION,
            variable_values={
                "input": {
                    "userId": base64_value("User"),
                    "productId": to_global_id("User", "7"),
                }
            },
        )
        assert (
            "must contain a node type and a local ID"
            in missing_separator_result.errors[0].message
        )
        assert received_inputs == []

    def maps_aliased_public_types_to_internal_types():
        account_id_type = global_id_scalar(
            "AccountID", ["Account"], aliases={"User": "Account"}
        )
        product_id_type = global_id_scalar("ProductID", ["Product"])
        schema, received_inputs = make_schema(account_id_type, product_id_type)

        result = graphql_sync(
            schema,
            VARIABLE_MUTATION,
            variable_values={
                "input": {
                    "userId": to_global_id("User", "42"),
                    "productId": to_global_id("Product", "7"),
                }
            },
        )

        assert result == ({"change": "ok"}, None)
        assert received_inputs[0]["userId"] == ResolvedGlobalId("Account", "42")

    def rejects_empty_types_empty_aliases_and_unknown_alias_targets():
        with raises(TypeError, match="at least one node type"):
            global_id_scalar("StrictID", [])
        with raises(TypeError, match="non-empty strings"):
            global_id_scalar("StrictID", [""])
        with raises(TypeError, match="non-empty strings"):
            global_id_scalar("StrictID", ["User"], aliases={"": "User"})
        with raises(TypeError, match="non-empty strings"):
            global_id_scalar("StrictID", ["User"], aliases={"Account": ""})
        with raises(TypeError, match="references unknown node type"):
            global_id_scalar("StrictID", ["User"], aliases={"Account": "Profile"})

    def rejects_non_strings_and_conflicts_within_one_scalar():
        scalar = global_id_scalar("StrictID", ["User"])
        with raises(GraphQLError, match="must be a string"):
            scalar.parse_value(42)
        with raises(TypeError, match="cannot be mapped to both"):
            global_id_scalar(
                "StrictID",
                ["User", "Account"],
                aliases={"User": "Account"},
            )

    def rejects_conflicting_alias_mappings_when_building_schema():
        account_id_type = global_id_scalar(
            "AccountID", ["Account"], aliases={"User": "Account"}
        )
        user_id_type = global_id_scalar("UserID", ["User"])

        with raises(TypeError, match="public node type 'User'"):
            make_schema(account_id_type, user_id_type)
