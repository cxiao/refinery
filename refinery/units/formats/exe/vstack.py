#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import List, TYPE_CHECKING

from refinery.units import Arg, Unit
from refinery.lib.executable import align, Arch, Executable
from refinery.lib.types import bounds

from dataclasses import dataclass, field

if TYPE_CHECKING:
    from typing import Tuple, Optional, Iterator
    from capstone import Cs
    from unicorn import Uc
    from intervaltree import IntervalTree, Interval


@dataclass
class EmuState:
    executable: Executable
    writes: IntervalTree
    address: int
    disassembler: Optional[Cs] = None
    waiting: int = 0
    callstack: List[int] = field(default_factory=list)
    retaddr: Optional[int] = None
    stop: Optional[int] = None
    previous_address: int = 0
    sp_register: int = 0
    stack_ceiling: int = 0

    def disassemble(self, address: int, size: int):
        if self.disassembler is None:
            return None
        try:
            pos = self.executable.location_from_address(address).physical.position
            end = pos + size
            return next(self.disassembler.disasm(
                bytes(self.executable.data[pos:end]), address, 1))
        except Exception:
            return None

    def fmt(self, address: int) -> str:
        return F'0x{address:0{self.executable.pointer_size//4}X}'


class vstack(Unit):
    """
    The unit emulates instructions at a given address in the input executable (PE/ELF/MachO) and
    extracts data patches that are written to the stack during emulation. Emulation is halted as
    soon as a certain number of instructions has not performed any memory writes, or when an error
    occurs.
    """

    @Unit.Requires('intervaltree')
    def _intervaltree():
        import intervaltree
        return intervaltree

    @Unit.Requires('unicorn')
    def _unicorn():
        import unicorn
        import unicorn.x86_const
        import unicorn.arm64_const
        import unicorn.mips_const
        import unicorn.sparc_const
        return unicorn

    @Unit.Requires('capstone')
    def _capstone():
        import capstone
        return capstone

    def __init__(
        self,
        *address: Arg.Number(metavar='start', help='Specify the (virtual) addresses of a stack string instruction sequences.'),
        stop: Arg.Number(metavar='stop', help='Optional: Stop when reaching this address.') = None,
        base: Arg.Number('-b', metavar='ADDR', help='Optionally specify a custom base address B.') = None,
        patch_range: Arg.Bounds('-p', metavar='MIN:MAX',
            help='Extract only patches that are in the given range, default is {default}.') = slice(5, None),
        write_range: Arg.Bounds('-n', metavar='MIN:MAX',
            help='Log only writes whose size is in the given range, default is {default}.') = slice(1, None),
        wait: Arg.Number('-w', help=(
            'When this many instructions did not write to memory, emulation is halted. The default is {default}.')) = 10,
        calls_wait: Arg.Switch('-c', help='Wait indefinitely when inside a function call.') = False,
        stack_size: Arg.Number('-s', help='Optionally specify the stack size. The default is 0x{default:X}.') = 0x10000,
        block_size: Arg.Number('-k', help='Standard memory block size for the emulator, 0x{default:X} by default.') = 0x1000,
    ):
        super().__init__(
            address=address,
            stop=stop,
            base=base,
            patch_range=patch_range,
            write_range=write_range,
            wait=wait,
            stack_size=stack_size,
            calls_wait=calls_wait,
            block_size=block_size,
        )

    def _find_stack_location(self, exe: Executable):
        stack_size = self.args.stack_size
        memory_max = 1 << exe.pointer_size
        space = exe.image_defined_address_space()
        aligned = align(stack_size, space.upper)
        if aligned + stack_size < memory_max:
            return aligned
        aligned = align(stack_size, space.lower - stack_size, down=True)
        if aligned > 0:
            return aligned
        raise RuntimeError('The primitive method used to map stack memory has failed.')

    def process(self, data):
        uc = self._unicorn
        exe = Executable.Load(data, self.args.base)
        arch = exe.arch()
        block_size = self.args.block_size
        stack_size = self.args.stack_size
        stack_addr = self._find_stack_location(exe)
        image = memoryview(data)
        disassembler = self._capstone.Cs(*self._cs_arch(arch))

        for address in self.args.address:

            emulator = uc.Uc(*self._uc_arch(arch))

            sp = {
                Arch.X8632   : uc.x86_const.UC_X86_REG_ESP,
                Arch.X8664   : uc.x86_const.UC_X86_REG_RSP,
                Arch.ARM32   : uc.arm_const.UC_ARM_REG_SP,
                Arch.ARM32   : uc.arm_const.UC_ARM_REG_SP,
                Arch.MIPS16  : uc.mips_const.UC_MIPS_REG_SP,
                Arch.MIPS32  : uc.mips_const.UC_MIPS_REG_SP,
                Arch.MIPS64  : uc.mips_const.UC_MIPS_REG_SP,
                Arch.SPARC32 : uc.sparc_const.UC_SPARC_REG_SP,
                Arch.SPARC64 : uc.sparc_const.UC_SPARC_REG_SP,
            }[arch]

            emulator.mem_map(stack_addr, stack_size * 3)
            emulator.reg_write(sp, stack_addr + 2 * stack_size)

            if arch is Arch.X8632:
                for reg in [
                    uc.x86_const.UC_X86_REG_EAX,
                    uc.x86_const.UC_X86_REG_EBX,
                    uc.x86_const.UC_X86_REG_ECX,
                    uc.x86_const.UC_X86_REG_EDX,
                    uc.x86_const.UC_X86_REG_ESI,
                    uc.x86_const.UC_X86_REG_EDI,
                    uc.x86_const.UC_X86_REG_EBP,
                ]:
                    emulator.reg_write(reg, stack_addr + stack_size)
            if arch is Arch.X8664:
                for reg in [
                    uc.x86_const.UC_X86_REG_RAX,
                    uc.x86_const.UC_X86_REG_RBX,
                    uc.x86_const.UC_X86_REG_RCX,
                    uc.x86_const.UC_X86_REG_RDX,
                    uc.x86_const.UC_X86_REG_RSI,
                    uc.x86_const.UC_X86_REG_RDI,
                    uc.x86_const.UC_X86_REG_RBP,
                    uc.x86_const.UC_X86_REG_R8,
                    uc.x86_const.UC_X86_REG_R9,
                    uc.x86_const.UC_X86_REG_R10,
                    uc.x86_const.UC_X86_REG_R11,
                    uc.x86_const.UC_X86_REG_R12,
                    uc.x86_const.UC_X86_REG_R13,
                    uc.x86_const.UC_X86_REG_R14,
                    uc.x86_const.UC_X86_REG_R15,
                ]:
                    emulator.reg_write(reg, stack_addr + stack_size)

            for segment in exe.segments():
                pmem = segment.physical
                vmem = segment.virtual
                try:
                    emulator.mem_map(vmem.lower, align(block_size, len(vmem)))
                    emulator.mem_write(vmem.lower, bytes(image[pmem.slice()]))
                except KeyboardInterrupt:
                    raise
                except Exception as error:
                    if address in vmem:
                        raise
                    width = exe.pointer_size // 4
                    self.log_info(F'error mapping segment [{vmem.lower:0{width}X}-{vmem.upper:0{width}X}]: {error!s}')

            tree = self._intervaltree.IntervalTree()
            state = EmuState(exe, tree, address, disassembler, stop=self.args.stop, sp_register=sp)

            emulator.hook_add(uc.UC_HOOK_CODE, self._hook_code, user_data=state)
            emulator.hook_add(uc.UC_HOOK_MEM_WRITE, self._hook_mem_write, user_data=state)
            emulator.hook_add(uc.UC_HOOK_INSN_INVALID, self._hook_insn_error, user_data=state)
            emulator.hook_add(uc.UC_HOOK_MEM_INVALID, self._hook_mem_error, user_data=state)

            end_of_code = exe.location_from_address(address).virtual.box.upper

            try:
                emulator.emu_start(address, end_of_code)
            except uc.UcError:
                pass

            it: Iterator[Interval] = iter(tree)
            for interval in it:
                size = interval.end - interval.begin - 1
                if size not in bounds[self.args.patch_range]:
                    continue
                self.log_info(F'memory patch at {state.fmt(interval.begin)} of size {size}')
                yield emulator.mem_read(interval.begin, size)

    def _hook_mem_write(self, emu: Uc, access: int, address: int, size: int, value: int, state: EmuState):
        mask = (1 << (size * 8)) - 1
        unsigned_value = value & mask
        depth = len(state.callstack)

        if unsigned_value == state.address:
            callstack = state.callstack
            if not callstack:
                state.stack_ceiling = emu.reg_read(state.sp_register)
            state.retaddr = unsigned_value
            callstack.append(unsigned_value)
        else:
            state.retaddr = None

        if state.stack_ceiling > 0 and address in range(state.stack_ceiling - 0x200, state.stack_ceiling):
            return

        state.waiting = 0

        if size not in bounds[self.args.write_range]:
            return

        state.writes.addi(address, address + size + 1)
        state.writes.merge_overlaps()

        def info():
            data = unsigned_value.to_bytes(size, state.executable.byte_order().value).hex().upper()
            indent = '\x20' * 4 * depth
            return (
                F'emulating [wait={state.waiting:02d}] {indent}{state.fmt(state.previous_address)}: '
                F'{state.fmt(address)} <- {data}')

        self.log_info(info)

    def _hook_insn_error(self, emu: Uc, state: EmuState):
        self.log_debug('aborting emulation; instruction error')
        emu.emu_stop()
        return False

    def _hook_mem_error(self, emu: Uc, access: int, address: int, size: int, value: int, state: EmuState):
        bs = self.args.block_size
        emu.mem_map(align(bs, address, down=True), 2 * bs)
        return True

    def _hook_code(self, emu: Uc, address: int, size: int, state: EmuState):
        if address == state.stop:
            emu.emu_stop()
            return False
        try:
            waiting = state.waiting
            callstack = state.callstack
            depth = len(callstack)
            state.previous_address = state.address

            if address != state.address:
                if depth and address == callstack[-1]:
                    depth -= 1
                    state.callstack.pop()
                    if depth == 0:
                        state.stack_ceiling = 0
                state.address = address
            elif state.retaddr is not None:
                # The present address was moved to the stack but we did not branch.
                # This is not quite accurate, of course: We could be calling the
                # next instruction. However, that sort of code is usually not really
                # a function call anyway, but rather a way to get the IP.
                callstack.pop()
                state.retaddr = None

            if waiting > self.args.wait:
                emu.emu_stop()
                return False
            if not depth or not self.args.calls_wait:
                state.waiting += 1
            state.address += size

            instruction = state.disassemble(address, size)
            indent = '\x20' * 4 * depth
            logmsg = F'emulating [wait={waiting:02d}] {indent}0x{address:0{state.executable.pointer_size//4}X}:'
            if instruction:
                instruction = F'{instruction.mnemonic} {instruction.op_str}'
                self.log_debug(logmsg, instruction)
            else:
                self.log_debug(logmsg, 'unrecognized instruction, aborting')
                emu.emu_stop()

        except KeyboardInterrupt:
            emu.emu_stop()
            return False

    def _uc_arch(self, arch: Arch) -> Tuple[int, int]:
        uc = self._unicorn
        return {
            Arch.X8632   : (uc.UC_ARCH_X86,   uc.UC_MODE_32),     # noqa
            Arch.X8664   : (uc.UC_ARCH_X86,   uc.UC_MODE_64),     # noqa
            Arch.ARM32   : (uc.UC_ARCH_ARM,   uc.UC_MODE_ARM),    # noqa
            Arch.ARM64   : (uc.UC_ARCH_ARM,   uc.UC_MODE_THUMB),  # noqa
            Arch.MIPS16  : (uc.UC_ARCH_MIPS,  uc.UC_MODE_16),     # noqa
            Arch.MIPS32  : (uc.UC_ARCH_MIPS,  uc.UC_MODE_32),     # noqa
            Arch.MIPS64  : (uc.UC_ARCH_MIPS,  uc.UC_MODE_64),     # noqa
            Arch.PPC32   : (uc.UC_ARCH_PPC,   uc.UC_MODE_32),     # noqa
            Arch.PPC64   : (uc.UC_ARCH_PPC,   uc.UC_MODE_64),     # noqa
            Arch.SPARC32 : (uc.UC_ARCH_SPARC, uc.UC_MODE_32),     # noqa
            Arch.SPARC64 : (uc.UC_ARCH_SPARC, uc.UC_MODE_V9),     # noqa
        }[arch]

    def _cs_arch(self, arch: Arch) -> Tuple[int, int]:
        cs = self._capstone
        return {
            Arch.X8632   : (cs.CS_ARCH_X86,   cs.CS_MODE_32),     # noqa
            Arch.X8664   : (cs.CS_ARCH_X86,   cs.CS_MODE_64),     # noqa
            Arch.ARM32   : (cs.CS_ARCH_ARM,   cs.CS_MODE_ARM),    # noqa
            Arch.ARM64   : (cs.CS_ARCH_ARM,   cs.CS_MODE_THUMB),  # noqa
            Arch.MIPS16  : (cs.CS_ARCH_MIPS,  cs.CS_MODE_16),     # noqa
            Arch.MIPS32  : (cs.CS_ARCH_MIPS,  cs.CS_MODE_32),     # noqa
            Arch.MIPS64  : (cs.CS_ARCH_MIPS,  cs.CS_MODE_64),     # noqa
            Arch.PPC32   : (cs.CS_ARCH_PPC,   cs.CS_MODE_32),     # noqa
            Arch.PPC64   : (cs.CS_ARCH_PPC,   cs.CS_MODE_64),     # noqa
            Arch.SPARC32 : (cs.CS_ARCH_SPARC, cs.CS_MODE_32),     # noqa
            Arch.SPARC64 : (cs.CS_ARCH_SPARC, cs.CS_MODE_V9),     # noqa
        }[arch]
