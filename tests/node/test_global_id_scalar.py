from pytest import fixture, raises

from graphql import (
    graphql_sync,
    GraphQLField,
    GraphQLInputField,
    GraphQLInputObjectType,
    GraphQLInt,
    GraphQLList,
    GraphQLNonNull,
    GraphQLObjectType,
    GraphQLSchema,
    GraphQLString,
)

from graphql_relay import (
    from_global_id,
    global_id_scalar,
    mutation_with_client_mutation_id,
    to_global_id,
    ResolvedGlobalId,
)


@fixture
def calls():
    """Records the keyword arguments the business resolver receives."""
    return []


@fixture
def schema(calls):
    """A mutation with two different global ID types in a nested input."""
    user_id = global_id_scalar("User", name="UserId")
    product_id = global_id_scalar(
        "Product", "Item", aliases={"LegacyItem": "Item"}, name="ProductId"
    )

    item_input = GraphQLInputObjectType(
        "ItemInput",
        {
            "productId": GraphQLInputField(GraphQLNonNull(product_id)),
            "quantity": GraphQLInputField(GraphQLInt),
        },
    )

    def mutate_and_get_payload(_info, **kwargs):
        calls.append(kwargs)
        user = kwargs["userId"]
        items = ", ".join(
            f"{item['productId'].type}:{item['productId'].id}"
            for item in kwargs["items"]
        )
        auditor = kwargs.get("auditorId")
        summary = f"{user.type}:{user.id} [{items}]"
        if auditor is not None:
            summary += f" audited by {auditor.type}:{auditor.id}"
        if "note" in kwargs:
            summary += f" note={kwargs['note']!r}"
        return {"summary": summary}

    mutation = mutation_with_client_mutation_id(
        "UpdateInventory",
        input_fields={
            "userId": GraphQLInputField(GraphQLNonNull(user_id)),
            "items": GraphQLInputField(
                GraphQLNonNull(GraphQLList(GraphQLNonNull(item_input)))
            ),
            "auditorId": GraphQLInputField(
                user_id, default_value=ResolvedGlobalId("User", "0")
            ),
            "note": GraphQLInputField(GraphQLString),
        },
        output_fields={"summary": GraphQLField(GraphQLString)},
        mutate_and_get_payload=mutate_and_get_payload,
    )
    query_type = GraphQLObjectType("Query", {"dummy": GraphQLField(GraphQLInt)})
    mutation_type = GraphQLObjectType("Mutation", {"updateInventory": mutation})
    return GraphQLSchema(query_type, mutation_type)


MUTATION = """
    mutation UpdateInventory($input: UpdateInventoryInput!) {
        updateInventory(input: $input) {
            summary
        }
    }
    """

USER_1 = to_global_id("User", "1")
USER_2 = to_global_id("User", "2")
PRODUCT_9 = to_global_id("Product", "9")
LEGACY_ITEM_3 = to_global_id("LegacyItem", "3")


def valid_variables():
    return {
        "input": {
            "userId": USER_1,
            "items": [
                {"productId": PRODUCT_9, "quantity": 2},
                {"productId": LEGACY_ITEM_3, "quantity": 1},
            ],
        }
    }


def describe_global_id_scalar():
    def decodes_variables_in_nested_input_objects_and_lists(schema, calls):
        result = graphql_sync(schema, MUTATION, variable_values=valid_variables())
        assert not result.errors
        assert result.data == {
            "updateInventory": {
                "summary": "User:1 [Product:9, Item:3] audited by User:0"
            }
        }
        # the business function received structured IDs, not unchecked strings
        (kwargs,) = calls
        assert kwargs["userId"] == ResolvedGlobalId("User", "1")
        assert kwargs["items"] == [
            {"productId": ResolvedGlobalId("Product", "9"), "quantity": 2},
            {"productId": ResolvedGlobalId("Item", "3"), "quantity": 1},
        ]

    def decodes_inline_literals(schema, calls):
        source = f"""
            mutation {{
                updateInventory(input: {{
                    userId: "{USER_2}",
                    items: [{{productId: "{PRODUCT_9}", quantity: 5}}],
                    note: "inline",
                }}) {{
                    summary
                }}
            }}
            """
        result = graphql_sync(schema, source)
        assert not result.errors
        assert result.data == {
            "updateInventory": {
                "summary": "User:2 [Product:9] audited by User:0 note='inline'"
            }
        }
        (kwargs,) = calls
        assert kwargs["userId"] == ResolvedGlobalId("User", "2")
        assert kwargs["items"][0]["productId"] == ResolvedGlobalId("Product", "9")

    def omitted_fields_do_not_become_none(schema, calls):
        result = graphql_sync(schema, MUTATION, variable_values=valid_variables())
        assert not result.errors
        (kwargs,) = calls
        # an omitted field without default is absent, not None
        assert "note" not in kwargs
        # an omitted field with a default gets the default, not None
        assert kwargs["auditorId"] is not None
        assert kwargs["auditorId"] == ResolvedGlobalId("User", "0")

    def rejects_invalid_base64_before_the_resolver_runs(schema, calls):
        variables = valid_variables()
        variables["input"]["userId"] = "not!valid!base64"
        result = graphql_sync(schema, MUTATION, variable_values=variables)
        assert result.data is None
        (error,) = result.errors
        assert "at 'input.userId'" in error.message
        assert "not a valid Base64 string" in error.message
        assert calls == []

    def rejects_empty_type_before_the_resolver_runs(schema, calls):
        variables = valid_variables()
        variables["input"]["userId"] = to_global_id("", "5")
        result = graphql_sync(schema, MUTATION, variable_values=variables)
        assert result.data is None
        (error,) = result.errors
        assert "at 'input.userId'" in error.message
        assert "empty type" in error.message
        assert calls == []

    def rejects_unknown_type_before_the_resolver_runs(schema, calls):
        variables = valid_variables()
        variables["input"]["userId"] = to_global_id("Order", "5")
        result = graphql_sync(schema, MUTATION, variable_values=variables)
        assert result.data is None
        (error,) = result.errors
        assert "at 'input.userId'" in error.message
        assert "Unknown type 'Order'" in error.message
        assert calls == []

    def rejects_a_product_id_used_as_user_id(schema, calls):
        variables = valid_variables()
        variables["input"]["userId"] = PRODUCT_9
        result = graphql_sync(schema, MUTATION, variable_values=variables)
        assert result.data is None
        (error,) = result.errors
        assert "at 'input.userId'" in error.message
        assert "Unknown type 'Product'" in error.message
        assert calls == []

    def reports_which_nested_list_item_is_invalid(schema, calls):
        variables = valid_variables()
        # the second item carries a User ID instead of a Product/Item ID
        variables["input"]["items"][1]["productId"] = USER_2
        result = graphql_sync(schema, MUTATION, variable_values=variables)
        assert result.data is None
        (error,) = result.errors
        assert "at 'input.items[1].productId'" in error.message
        assert "Unknown type 'User'" in error.message
        assert calls == []

    def rejects_invalid_inline_literals_before_the_resolver_runs(schema, calls):
        source = """
            mutation {
                updateInventory(input: {
                    userId: "!!!",
                    items: [],
                }) {
                    summary
                }
            }
            """
        result = graphql_sync(schema, source)
        assert result.data is None
        assert result.errors
        assert "not a valid Base64 string" in result.errors[0].message
        assert calls == []

    def maps_aliased_public_types_to_the_internal_type(schema, calls):
        result = graphql_sync(schema, MUTATION, variable_values=valid_variables())
        assert not result.errors
        (kwargs,) = calls
        # "LegacyItem" is a public alias for the internal type "Item"
        assert kwargs["items"][1]["productId"] == ResolvedGlobalId("Item", "3")

    def supports_multiple_public_types_migrating_to_one_internal_type():
        scalar = global_id_scalar(
            "User", aliases={"Customer": "User", "Account": "User"}
        )
        assert scalar.parse_value(to_global_id("User", "1")) == ResolvedGlobalId(
            "User", "1"
        )
        assert scalar.parse_value(to_global_id("Customer", "2")) == ResolvedGlobalId(
            "User", "2"
        )
        assert scalar.parse_value(to_global_id("Account", "3")) == ResolvedGlobalId(
            "User", "3"
        )

    def rejects_conflicting_alias_configuration_when_building_the_schema():
        with raises(ValueError, match="Conflicting configuration"):
            # "User" cannot be both an allowed type and an alias
            global_id_scalar("User", aliases={"User": "Account"})
        with raises(ValueError, match="Conflicting configuration"):
            # alias target must be one of the allowed types
            global_id_scalar("User", aliases={"Customer": "Account"})
        with raises(ValueError, match="Conflicting configuration"):
            global_id_scalar("User", "User")
        with raises(TypeError):
            global_id_scalar()

    def from_global_id_keeps_its_lenient_behavior():
        assert from_global_id("not-base64") == ResolvedGlobalId("", "")
        assert from_global_id(to_global_id("User", "1")) == ResolvedGlobalId(
            "User", "1"
        )
