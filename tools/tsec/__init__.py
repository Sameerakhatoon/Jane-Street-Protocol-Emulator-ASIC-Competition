"""tsec: the TSE timed-spec compiler.

Pipeline, one module per stage:
  lexer -> parser (ast, printer) -> resolve -> check -> semantics -> lower
  -> schedule (timing) -> codegen -> report, uppaal, proofgen
isa.py is the single seam to the ISA tables.
"""
