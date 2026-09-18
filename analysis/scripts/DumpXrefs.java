import java.io.*;
import java.nio.charset.StandardCharsets;

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.symbol.Reference;

public class DumpXrefs extends GhidraScript {
	@Override
	protected void run() throws Exception {
		if (getScriptArgs().length < 2) {
			printerr("Usage: DumpXrefs <target_addr_hex> <out_file>");
			return;
		}
		long targetValue = Long.decode(getScriptArgs()[0]);
		String outPath = getScriptArgs()[1];
		Address target = toAddr(targetValue);
		Listing listing = currentProgram.getListing();
		try (PrintWriter out = new PrintWriter(new OutputStreamWriter(new FileOutputStream(outPath), StandardCharsets.UTF_8))) {
			out.println("# Xrefs to " + target);
			for (Reference ref : getReferencesTo(target)) {
				Address from = ref.getFromAddress();
				Function f = getFunctionContaining(from);
				out.println();
				out.println("## " + from + " -> " + target + " (" + ref.getReferenceType() + ")");
				if (f != null) {
					out.println("function: " + f.getName() + " @ " + f.getEntryPoint());
				}
				Instruction ins = listing.getInstructionAt(from);
				if (ins == null) {
					out.println("no instruction at ref");
					continue;
				}
				Instruction start = ins;
				for (int i = 0; i < 8 && start.getPrevious() != null; i++) {
					start = start.getPrevious();
				}
				Instruction cur = start;
				for (int i = 0; i < 18 && cur != null; i++, cur = cur.getNext()) {
					String mark = cur.getAddress().equals(from) ? ">" : " ";
					out.printf("%s %s: %-8s %s%n", mark, cur.getAddress(), cur.getMnemonicString(), ops(cur));
				}
			}
		}
	}

	private String ops(Instruction ins) {
		StringBuilder sb = new StringBuilder();
		for (int i = 0; i < ins.getNumOperands(); i++) {
			if (i > 0) sb.append(", ");
			sb.append(ins.getDefaultOperandRepresentation(i));
		}
		return sb.toString();
	}
}
