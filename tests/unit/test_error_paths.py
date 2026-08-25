"""Turning what Error Tracking names into somewhere in a repository (M8 4.1)."""

from triage.errors.paths import enclosing_function, source_location


def test_a_fully_qualified_scala_class_becomes_a_package_path():
    """The measured shape: 202 of 202 issues name the class, none names a path."""
    located = source_location("zeenea.repository.orientdb.OdbClient.scala", "$anonfun$load$6")

    assert "src/main/scala/zeenea/repository/orientdb/OdbClient.scala" in located.paths
    assert "zeenea/repository/orientdb/OdbClient.scala" in located.paths
    assert located.derived
    assert located.caveat is not None
    assert "fully-qualified class name" in located.caveat


def test_the_java_and_kotlin_source_roots_follow_their_own_suffix():
    assert "src/main/java/com/acme/Api.java" in source_location("com.acme.Api.java", None).paths
    assert "src/main/kotlin/com/acme/Api.kt" in source_location("com.acme.Api.kt", None).paths


def test_a_path_datadog_already_gave_as_a_path_is_left_alone():
    located = source_location("src/payments/idempotency.py", "flush")

    assert located.paths == ("src/payments/idempotency.py",)
    assert not located.derived
    assert located.caveat is None


def test_a_bare_file_name_is_looked_for_anywhere_and_says_so():
    located = source_location("OdbClient.scala", None)

    assert located.paths == ("OdbClient.scala",)
    assert located.derived
    assert located.caveat is not None


def test_no_file_is_an_absence_with_a_reason_rather_than_an_empty_list():
    located = source_location(None, "load")

    assert located.paths == ()
    assert located.caveat is not None
    assert "named no file" in located.caveat


def test_a_scala_lambda_names_the_method_it_was_written_inside():
    assert enclosing_function("$anonfun$load$6") == "load"
    assert enclosing_function("$anonfun$load$6$adapted") == "load"
    assert enclosing_function("lambda$load$3") == "load"


def test_a_plain_method_name_is_its_own_enclosing_function():
    assert enclosing_function("loadEntity") == "loadEntity"
    assert enclosing_function(None) is None
