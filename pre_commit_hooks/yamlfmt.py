#!/usr/bin/env python3
"""Format YAML files."""

import argparse
import sys
from ruamel.yaml import YAML  # pylint: disable=import-error
from ruamel.yaml.comments import (  # pylint: disable=import-error
    CommentedMap,
    CommentedSeq,
    )
from ruamel.yaml.scalarstring import (  # pylint: disable=import-error
    DoubleQuotedScalarString,
    SingleQuotedScalarString,
    )

DEFAULT_INDENT = {
    "mapping": 4,
    "sequence": 6,
    "offset": 4,
    }

# Rendered width of "[" + "]" and of the ", " between two items.
BRACKET_WIDTH = 2
SEPARATOR_WIDTH = 2


class Cli:
    # pylint: disable=too-few-public-methods
    """Command-line-interface."""

    def __init__(self):
        """Initialize the CLI."""
        parser = argparse.ArgumentParser(
            description="Format YAML files",
            formatter_class=argparse.ArgumentDefaultsHelpFormatter,
            epilog="Tips at https://yaml.readthedocs.io/en/latest/detail.html",
            )
        parser.add_argument(
            "-m",
            "--mapping",
            type=int,
            default=DEFAULT_INDENT["mapping"],
            help="number of spaces to indent mappings (dictionaries)",
            )
        parser.add_argument(
            "-s",
            "--sequence",
            type=int,
            default=DEFAULT_INDENT["sequence"],
            help="number of spaces to indent sequences (arrays/lists)",
            )
        parser.add_argument(
            "-o",
            "--offset",
            type=int,
            default=DEFAULT_INDENT["offset"],
            help="number of spaces to offset the dash from sequences",
            )
        parser.add_argument(
            "-c",
            "--colons",
            action="store_true",
            help="whether to align top-level colons",
            )
        parser.add_argument(
            "-w",
            "--width",
            type=int,
            default=150,
            help="maximum line width",
            )
        parser.add_argument(
            "--max-flow-width",
            type=int,
            default=0,
            help="rewrite flow sequences ([a, b]) wider than this as block "
                 "sequences; 0 disables the rewrite",
            )
        parser.add_argument(
            "-p",
            "--preserve-quotes",
            action="store_true",
            help="whether to keep existing string quoting"
            )

        document_start = parser.add_mutually_exclusive_group()
        document_start.add_argument(
            "--implicit_start",
            "-e",
            action="store_false",
            dest="explicit_start",
            default=argparse.SUPPRESS,
            help="whether to remove the explicit document start"
            )
        document_start.add_argument(
            "--explicit_start",
            action="store_true",
            dest="explicit_start",
            default=True,
            help="whether to add the explicit document start"
            )

        document_end = parser.add_mutually_exclusive_group()
        document_end.add_argument(
            "--implicit_end",
            action="store_false",
            dest="explicit_end",
            default=argparse.SUPPRESS,
            help="whether to remove the explicit document end"
            )
        document_end.add_argument(
            "--explicit_end",
            action="store_true",
            dest="explicit_end",
            default=False,
            help="whether to add the explicit document end"
            )

        parser.add_argument(
            "-n",
            "--preserve_null",
            action="store_true",
            help="whether to keep null values"
            )
        parser.add_argument(
            "file_names",
            metavar="FILE_NAME",
            nargs="*",
            help="space-separated list of YAML file names",
            )

        self.parser = parser


def scalar_width(item):
    """Approximate the rendered width of a scalar inside a flow sequence."""
    if isinstance(item, (SingleQuotedScalarString, DoubleQuotedScalarString)):
        return len(item) + 2  # the surrounding quote characters
    if item is None:
        return len("null")
    if isinstance(item, bool):
        return len("true") if item else len("false")
    return len(str(item))


class FlowSequenceExpander:
    # pylint: disable=too-few-public-methods
    """
    Rewrite too-wide flow sequences as block sequences.

    ruamel preserves flow style but re-flows it onto a single line, so a
    hand-wrapped "[a, b, c]" spread over several lines collapses into one
    long line. Block sequences round-trip unchanged at any width, so they
    are the only stable way to keep a long list readable.

    Only sequences whose items all survive the move unchanged are rewritten
    (see _is_rewritable): for those the rendered width can be predicted
    exactly, and the result is guaranteed to stay one item per line.
    """

    def __init__(self, max_width, mapping, sequence):
        """Initialize the expander with the formatter's indent settings."""
        self.max_width = max_width
        self.mapping = mapping
        self.sequence = sequence

    def expand(self, node):
        """Rewrite every too-wide flow sequence reachable from node."""
        self._visit(node, 0)

    def _visit(self, node, column):
        """Walk node, tracking the column its content is rendered at."""
        if isinstance(node, CommentedMap):
            for key, value in node.items():
                # A block child is indented from the mapping; a flow child is
                # rendered inline, just past "key: ".
                self._visit_child(
                    value,
                    inline=column + len(str(key)) + 2,
                    block_map=column + self.mapping,
                    block_seq=column + self.sequence,
                    )
        elif isinstance(node, CommentedSeq):
            for item in node:
                # A mapping in a sequence item starts on the dash line itself.
                self._visit_child(
                    item,
                    inline=column,
                    block_map=column,
                    block_seq=column + self.sequence,
                    )

    def _visit_child(self, child, inline, block_map, block_seq):
        """Rewrite child if it is a wide flow sequence, else recurse."""
        if not isinstance(child, (CommentedMap, CommentedSeq)):
            return

        is_map = isinstance(child, CommentedMap)

        if child.fa.flow_style():
            # Block style is illegal inside flow style, so never descend into
            # a flow collection: only the outermost one may be rewritten.
            if not is_map and self._too_wide(child, inline):
                child.fa.set_block_style()
            return

        self._visit(child, block_map if is_map else block_seq)

    def _too_wide(self, seq, column):
        """Whether seq rendered inline at column would exceed the maximum."""
        if not seq or not all(self._is_rewritable(item) for item in seq):
            return False
        width = column + BRACKET_WIDTH + SEPARATOR_WIDTH * (len(seq) - 1)
        width += sum(scalar_width(item) for item in seq)
        return width > self.max_width

    @staticmethod
    def _is_rewritable(item):
        """Whether an item renders the same once moved out of flow style."""
        if isinstance(item, (CommentedMap, CommentedSeq)):
            # A nested collection's width cannot be predicted from the item
            # alone, so leave the whole sequence in flow style.
            return False
        # ruamel writes None as "null" in flow style but as an empty scalar in
        # block style, which would trade a readable list for trailing blanks.
        return item is not None


class Formatter:
    """
    Reformat a yaml file with proper indentation.

    Preserve comments.
    """

    def __init__(self, **kwargs):
        """Initialize the formatter."""
        yaml = YAML()
        yaml.indent(
            mapping=kwargs.get("mapping", DEFAULT_INDENT["mapping"]),
            sequence=kwargs.get("sequence", DEFAULT_INDENT["sequence"]),
            offset=kwargs.get("offset", DEFAULT_INDENT["offset"]),
            )
        yaml.top_level_colon_align = kwargs.get("colons", False)
        yaml.explicit_start = kwargs.get("explicit_start", True)
        yaml.explicit_end = kwargs.get("explicit_end", False)
        yaml.width = kwargs.get("width", None)
        yaml.preserve_quotes = kwargs.get("preserve_quotes", False)

        if kwargs.get("preserve_null"):
            def represent_none(self, _data):
                return self.represent_scalar('tag:yaml.org,2002:null', 'null')
            yaml.representer.add_representer(type(None), represent_none)

        max_flow_width = kwargs.get("max_flow_width", 0)
        self.expander = FlowSequenceExpander(
            max_width=max_flow_width,
            mapping=kwargs.get("mapping", DEFAULT_INDENT["mapping"]),
            sequence=kwargs.get("sequence", DEFAULT_INDENT["sequence"]),
            ) if max_flow_width > 0 else None

        self.yaml = yaml
        self.path = kwargs.get("path", None)
        self.content = list({})

    def format(self, path=None):
        """Read file and write it out to same path."""
        if not path:
            path = self.path
        print(path, end="")
        self.parse_file(path)  # pylint: disable=E0601
        self.expand_flow_sequences()
        self.write_file(path)
        print("  Done")

    def expand_flow_sequences(self):
        """Rewrite too-wide flow sequences as block sequences."""
        if not self.expander:
            return
        for document in self.content:
            self.expander.expand(document)

    def parse_file(self, path=None):
        """Read the file."""
        if not path:
            path = self.path
        try:
            with open(path, "r", encoding='utf-8') as stream:
                self.content = list(self.yaml.load_all(stream))
        except IOError:
            self.fail(f"Unable to read {path}")

    def write_file(self, path=None):
        """Write the file."""
        if not path:
            path = self.path
        try:
            with open(path, "w", encoding='utf-8') as stream:
                self.yaml.dump_all(self.content, stream)
        except IOError:
            self.fail(f"Unable to write {path}")

    @staticmethod
    def fail(msg):
        """Abort."""
        sys.stderr.write(msg)
        sys.exit(1)


def main():
    """Run formatter."""
    args = Cli().parser.parse_args()
    formatter = Formatter(
        mapping=args.mapping,
        sequence=args.sequence,
        offset=args.offset,
        colons=args.colons,
        width=args.width,
        max_flow_width=args.max_flow_width,
        preserve_quotes=args.preserve_quotes,
        preserve_null=args.preserve_null,
        explicit_start=args.explicit_start,
        explicit_end=args.explicit_end
        )
    for file_name in args.file_names:
        formatter.format(file_name)


if __name__ == "__main__":
    main()
