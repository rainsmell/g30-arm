// DumpProtocol.java
// Ghidra headless script: dump key decompiled functions and string xrefs.

import java.io.*;
import java.nio.charset.StandardCharsets;

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.symbol.Reference;

public class DumpProtocol extends GhidraScript {

	private static final long[] FUNCS = {
		0x08008b34L,
		0x08008ffcL,
		0x0800b2ccL,
	};

	private static final long[] STRINGS = {
		0x0800936fL, // " FunCode Wrong!\r\n"
		0x08009384L, // " CRC Wrong!\r\n"
		0x08001528L, // "Bootloader Version: %d.%d.%d"
		0x0800b0e8L, // "Firmware Version: %d.%d.%d"
		0x08001548L, // "Build info:%s, motor ID: %d."
		0x0800b108L, // "Build info:%s, motor ID: %d, crc:0x%08X."
	};

	@Override
	protected void run() throws Exception {
		String outPath = getScriptArgs().length > 0 ? getScriptArgs()[0] : "protocol_report.txt";
		File outFile = new File(outPath);
		try (PrintWriter out = new PrintWriter(new OutputStreamWriter(new FileOutputStream(outFile), "UTF-8"))) {
			out.println("# Protocol report");
			out.println("Program: " + currentProgram.getName());
			out.println();

			for (long addr : FUNCS) {
				dumpFunction(out, addr);
			}

			out.println();
			out.println("## String xrefs");
			for (long addr : STRINGS) {
				dumpStringXrefs(out, addr);
			}
		}
	}

	private void dumpFunction(PrintWriter out, long addrValue) throws Exception {
		Address addr = toAddr(addrValue);
		Function f = getFunctionContaining(addr);
		out.println("## Function @ " + addr);
		if (f == null) {
			out.println("not found");
			out.println();
			return;
		}
		out.println("name: " + f.getName());
		out.println("entry: " + f.getEntryPoint());
		out.println("signature: " + f.getSignature());
		out.println("body: " + f.getBody().getMinAddress() + " - " + f.getBody().getMaxAddress());
		out.println();
		Listing listing = currentProgram.getListing();
		InstructionIterator it = listing.getInstructions(f.getBody(), true);
		int count = 0;
		while (it.hasNext()) {
			Instruction ins = it.next();
			StringBuilder ops = new StringBuilder();
			int opCount = ins.getNumOperands();
			for (int i = 0; i < opCount; i++) {
				if (i > 0) {
					ops.append(", ");
				}
				ops.append(ins.getDefaultOperandRepresentation(i));
			}
			out.printf("%s: %-8s %s%n", ins.getAddress(), ins.getMnemonicString(), ops);
			count++;
			if (count >= 400) {
				out.println("... truncated ...");
				break;
			}
		}
		out.println();
	}

	private void dumpStringXrefs(PrintWriter out, long addrValue) throws Exception {
		Address found = toAddr(addrValue);
		out.println("### " + found);
		String text = readCString(found, 64);
		if (text == null) {
			out.println("not readable");
			out.println();
			return;
		}
		out.println("text: " + text);
		out.println("addr: " + found);
		out.println("xrefs:");
		for (Reference ref : getReferencesTo(found)) {
			out.println("  " + ref.getFromAddress() + " -> " + ref.getToAddress() + " (" + ref.getReferenceType() + ")");
		}
		out.println();
	}

	private String readCString(Address addr, int maxLen) throws Exception {
		Memory mem = currentProgram.getMemory();
		byte[] buf = new byte[maxLen];
		for (int i = 0; i < maxLen; i++) {
			byte b = mem.getByte(addr.add(i));
			if (b == 0) {
				return new String(buf, 0, i, StandardCharsets.UTF_8);
			}
			buf[i] = b;
		}
		return new String(buf, StandardCharsets.UTF_8);
	}
}
