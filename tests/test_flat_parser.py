import argparse
import pytest
from lantern import cli

def test_fleet_overview_parser_supports_flat():
    parser = cli.build_parser()
    args = parser.parse_args(["fleet", "overview", "--flat"])
    assert args.fleet_command == "overview"
    assert args.flat is True

def test_fleet_plan_parser_supports_flat():
    parser = cli.build_parser()
    args = parser.parse_args(["fleet", "plan", "--flat"])
    assert args.fleet_command == "plan"
    assert args.flat is True

def test_fleet_apply_parser_supports_flat():
    parser = cli.build_parser()
    args = parser.parse_args(["fleet", "apply", "--flat"])
    assert args.fleet_command == "apply"
    assert args.flat is True

def test_forge_clone_parser_supports_flat():
    parser = cli.build_parser()
    args = parser.parse_args(["forge", "clone", "--flat"])
    assert args.forge_command == "clone"
    assert args.flat is True
