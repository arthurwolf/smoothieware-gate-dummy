"""Board capacities for report formatting and canonical checker-result banks.

Section classification and size arithmetic belong to smoothie-sim's checker.
These retain the gate's existing capacity policy, including V1 debug overflow.
"""
LPC1768_CAPACITY = {'Flash': 512 * 1024, 'RAM': 32 * 1024, 'AHB RAM': 32 * 1024}
STM32H745_CAPACITY = {'Flash': 2048 * 1024, 'ITCM': 64 * 1024, 'DTCM': 128 * 1024,
                     'AXI SRAM': 512 * 1024, 'SRAM1': 128 * 1024, 'SRAM2': 128 * 1024,
                     'SRAM3': 32 * 1024, 'SRAM4': 64 * 1024}
CAPACITIES = {'lpc1769': LPC1768_CAPACITY, 'stm32h745': STM32H745_CAPACITY}
