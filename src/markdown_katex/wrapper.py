# This file is part of the markdown-katex project
# https://gitlab.com/mbarkhau/markdown-katex
#
# Copyright (c) 2019 Manuel Barkhau (mbarkhau@gmail.com) - MIT License
# SPDX-License-Identifier: MIT

# NOTE (mb 2019-05-16): This module is substantially shared with the
#   markdown-katex package and meaningful changes should be
#   replicated there also.

import os
import re
import time
import signal
import typing as typ
import hashlib
import platform
import tempfile
import subprocess as sp

import pathlib2 as pl

SIG_NAME_BY_NUM = {
    k: v
    for v, k in sorted(signal.__dict__.items(), reverse=True)
    if v.startswith('SIG') and not v.startswith('SIG_')
}

assert SIG_NAME_BY_NUM[15] == 'SIGTERM'


TMP_DIR = pl.Path(tempfile.gettempdir()) / "mdkatex"

LIBDIR: pl.Path = pl.Path(__file__).parent
PKG_BIN_DIR      = LIBDIR / "bin"
FALLBACK_BIN_DIR = pl.Path("/") / "usr" / "local" / "bin"
FALLBACK_BIN_DIR = FALLBACK_BIN_DIR.expanduser()

# NOTE (mb 2020-06-19): I have no idea if this is true and have not found a good
#   way to test it, especially not in any cross platform way. Maybe KaTeX doesn't
#   care and just uses the same encoding for input as for output.
KATEX_INPUT_ENCODING  = "UTF-8"
KATEX_OUTPUT_ENCODING = "UTF-8"

CMD_NAME = "katex"


# https://pymotw.com/3/platform/
OSNAME  = platform.system()
MACHINE = platform.machine()


def _get_usr_bin_path() -> typ.Optional[pl.Path]:
    env_path = os.environ.get('PATH')
    env_paths: typ.List[pl.Path] = []

    if env_path:
        path_strs = env_path.split(os.pathsep)
        env_paths.extend([pl.Path(p) for p in path_strs])

    # search in fallback bin dir regardless of path
    if FALLBACK_BIN_DIR not in env_paths:
        env_paths.append(FALLBACK_BIN_DIR)

    if OSNAME == 'Windows':
        local_bin_commands = [f"{CMD_NAME}.cmd", f"{CMD_NAME}.ps1", f"{CMD_NAME}.exe"]
    else:
        local_bin_commands = [CMD_NAME]

    for path in env_paths:
        for local_cmd in local_bin_commands:
            local_bin = path / local_cmd
            if local_bin.is_file():
                return local_bin

    return None


def _get_pkg_bin_path(osname: str = OSNAME, machine: str = MACHINE) -> pl.Path:
    if machine == 'AMD64':
        machine = 'x86_64'
    glob_expr = f"*_{machine}-{osname}"
    bin_files = list(PKG_BIN_DIR.glob(glob_expr))
    if bin_files:
        return max(bin_files)

    err_msg = (
        "Platform not supported, "
        "katex binary not found. "
        "Install manually using "
        "'npm install katex'."
    )

    raise NotImplementedError(err_msg)


def get_bin_path() -> pl.Path:
    usr_bin_path = _get_usr_bin_path()
    if usr_bin_path:
        return usr_bin_path
    else:
        return _get_pkg_bin_path()


def _iter_output_lines(buf: typ.IO[bytes]) -> typ.Iterable[bytes]:
    while True:
        output = buf.readline()
        if output:
            yield output
        else:
            return


def read_output(buf: typ.Optional[typ.IO[bytes]]) -> str:
    assert buf is not None
    return b"".join(_iter_output_lines(buf)).decode("utf-8")


ArgValue = typ.Union[str, int, float, bool]
Options  = typ.Dict[str, ArgValue]


def _iter_cmd_parts(options: Options = None) -> typ.Iterable[str]:
    yield str(get_bin_path())

    if options:
        for option_name, option_value in options.items():
            if option_name.startswith("--"):
                arg_name = option_name
            else:
                arg_name = "--" + option_name

            if option_value is True:
                yield arg_name
            elif option_value is False:
                continue
            else:
                arg_value = str(option_value)
                yield arg_name
                yield arg_value


def _cmd_digest(tex: str, cmd_parts: typ.List[str]) -> str:
    hasher = hashlib.sha256(tex.encode("utf-8"))
    for cmd_part in cmd_parts:
        hasher.update(cmd_part.encode("utf-8"))
    return hasher.hexdigest()


def _write_tex2html(cmd_parts: typ.List[str], tex: str, tmp_output_file: pl.Path) -> None:
    tmp_input_file = TMP_DIR / tmp_output_file.name.replace(".html", ".tex")
    input_data     = tex.encode(KATEX_INPUT_ENCODING)

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    with tmp_input_file.open(mode="wb") as fobj:
        fobj.write(input_data)

    cmd_parts.extend(["--input", str(tmp_input_file), "--output", str(tmp_output_file)])
    proc     = sp.Popen(cmd_parts, stdout=sp.PIPE, stderr=sp.PIPE)
    ret_code = proc.wait()
    if ret_code < 0:
        signame = SIG_NAME_BY_NUM[abs(ret_code)]
        err_msg = (
            f"Error processing '{tex}': "
            + "katex_cli process ended with "
            + f"code {ret_code} ({signame})"
        )
        raise Exception(err_msg)
    elif ret_code > 0:
        stdout  = read_output(proc.stdout)
        errout  = read_output(proc.stderr)
        output  = (stdout + "\n" + errout).strip()
        err_msg = f"Error processing '{tex}': {output}"
        raise Exception(err_msg)

    tmp_input_file.unlink()


def tex2html(tex: str, options: Options = None) -> str:
    cmd_parts       = list(_iter_cmd_parts(options))
    digest          = _cmd_digest(tex, cmd_parts)
    tmp_filename    = digest + ".html"
    tmp_output_file = TMP_DIR / tmp_filename

    try:
        if tmp_output_file.exists():
            # give cached file a life extension (update mtime)
            tmp_output_file.touch()
        else:
            _write_tex2html(cmd_parts, tex, tmp_output_file)

        with tmp_output_file.open(mode="r", encoding=KATEX_OUTPUT_ENCODING) as fobj:
            result = typ.cast(str, fobj.read())
            return result.strip()
    finally:
        _cleanup_tmp_dir()


def _cleanup_tmp_dir() -> None:
    min_mtime = time.time() - 24 * 60 * 60
    for fpath in TMP_DIR.iterdir():
        if not fpath.is_file():
            continue
        mtime = fpath.stat().st_mtime
        if mtime < min_mtime:
            fpath.unlink()


# NOTE: in order to not have to update the code
#   of the extension any time an option is added,
#   we parse the help text of the katex command.


DEFAULT_HELP_TEXT = r"""
Options:
  -V, --version              output the version number
  -d, --display-mode         Render math in display...
  --leqno                    Render display math in...
  --fleqn                    Render display math fl...
  -t, --no-throw-on-error    Render errors (in the ...
  -c, --error-color <color>  A color string given i...
  -b, --color-is-text-color  Makes \color behave li...
  -S, --strict               Turn on strict / LaTeX...
  -s, --max-size <n>         If non-zero, all user-...
  -e, --max-expand <n>       Limit the number of ma...
  -m, --macro <def>          Define custom macro of...
  -f, --macro-file <path>    Read macro definitions...
  -i, --input <path>         Read LaTeX input from ...
  -o, --output <path>        Write html output to t...
  -h, --help                 output usage information
"""

DEFAULT_HELP_TEXT = DEFAULT_HELP_TEXT.replace("\n", " ").replace("NL", "\n")


def _get_cmd_help_text() -> str:
    binpath   = get_bin_path()
    cmd_parts = [str(binpath), "--help"]
    proc      = sp.Popen(cmd_parts, stdout=sp.PIPE)
    help_text = read_output(proc.stdout)
    return help_text


OptionsHelp = typ.Dict[str, str]

# https://regex101.com/r/287NYS/4
OPTION_PATTERN = r"""
    --
    (?P<name>[a-z\-]+)
    \s+(?:<[a-z\-]+>)?
    \s+
    (?P<text>[^\n]*[ \s\w(){},:;.'\\/\[\] ]*)
"""
OPTION_REGEX = re.compile(OPTION_PATTERN, flags=re.VERBOSE | re.DOTALL | re.MULTILINE)


def _parse_options_help_text(help_text: str) -> OptionsHelp:
    options: OptionsHelp = {}

    options_text = help_text.split("Options:", 1)[-1]

    for match in OPTION_REGEX.finditer(options_text):
        name = match.group("name")
        text = match.group("text")
        text = " ".join(line.strip() for line in text.splitlines())
        options[name] = text.strip()

    options.pop("version"     , None)
    options.pop("help"        , None)
    options.pop("input"       , None)
    options.pop("output"      , None)
    options.pop("display-mode", None)

    return options


_PARSED_OPTIONS: OptionsHelp = {}


def parse_options() -> OptionsHelp:
    if _PARSED_OPTIONS:
        return _PARSED_OPTIONS

    options = _parse_options_help_text(DEFAULT_HELP_TEXT)
    try:
        help_text   = _get_cmd_help_text()
        cmd_options = _parse_options_help_text(help_text)
        options.update(cmd_options)
    except NotImplementedError:
        # NOTE: no need to fail just for the options
        pass

    _PARSED_OPTIONS.update(options)
    return options
