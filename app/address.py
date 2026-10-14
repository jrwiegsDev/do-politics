"""A street address as a visitor gives it: four fields, used once."""

from typing import Annotated, Self

from pydantic import BaseModel, StringConstraints, model_validator

Street = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=3, max_length=100)
]
City = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]
State = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, to_upper=True, pattern=r"^([A-Za-z]{2})?$"
    ),
]
ZipCode = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^([0-9]{5})?$")
]


class Address(BaseModel):
    """A street address. It is used once to find a district and never stored.

    A field left blank is an empty string, which is what a web form sends.
    """

    street: Street
    city: City = ""
    state: State = ""
    zip_code: ZipCode = ""

    @model_validator(mode="after")
    def has_a_zip_code_or_a_city_and_state(self) -> Self:
        """The Census needs a street with a ZIP code, or with a city and state."""
        if not self.zip_code and not (self.city and self.state):
            raise ValueError("Give a ZIP code, or a city and a state.")
        return self
