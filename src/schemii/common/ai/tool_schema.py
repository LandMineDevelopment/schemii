"""Project typed contracts into the portable provider tool-schema subset.

Pydantic discriminated unions are disjoint by operation tag, so anyOf retains
their meaning. Runtime validation still uses the original Pydantic contract.
"""
def provider_schema(value):
    if isinstance(value,list): return [provider_schema(item) for item in value]
    if not isinstance(value,dict): return value
    return {"anyOf" if key == "oneOf" else key:provider_schema(item)
            for key,item in value.items() if key != "discriminator"}
