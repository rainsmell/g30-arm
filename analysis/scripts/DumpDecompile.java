import java.io.*;
import java.nio.charset.StandardCharsets;

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileOptions;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;

public class DumpDecompile extends GhidraScript {

	@Override
	protected void run() throws Exception {
		if (getScriptArgs().length < 2) {
			printerr("Usage: DumpDecompile <func_addr_hex> <out_file>");
			return;
		}

		long addrValue = Long.decode(getScriptArgs()[0]);
		String outPath = getScriptArgs()[1];

		Address addr = toAddr(addrValue);
		Function func = getFunctionContaining(addr);
		if (func == null) {
			printerr("Function not found at " + addr);
			return;
		}

		DecompInterface decompiler = new DecompInterface();
		DecompileOptions opts = new DecompileOptions();
		opts.grabFromProgram(currentProgram);
		decompiler.setOptions(opts);
		decompiler.toggleCCode(true);
		decompiler.toggleSyntaxTree(true);
		decompiler.setSimplificationStyle("decompile");
		if (!decompiler.openProgram(currentProgram)) {
			printerr("Failed to open program");
			return;
		}

		DecompileResults res = decompiler.decompileFunction(func, 60, monitor);
		try (Writer w = new OutputStreamWriter(new FileOutputStream(outPath), StandardCharsets.UTF_8)) {
			w.write("# Function @ " + func.getEntryPoint() + "\n");
			w.write("# name: " + func.getName() + "\n\n");
			if (res == null || !res.decompileCompleted()) {
				w.write("DECOMPILATION FAILED\n");
				return;
			}
			w.write(res.getDecompiledFunction().getC());
		}
	}
}
