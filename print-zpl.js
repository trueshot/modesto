#!/usr/bin/env node
/**
 * Send raw ZPL file to Zebra ZT421 printer
 * Uses Windows printer API for raw data transmission
 * Usage: node print-zpl.js [filename]
 */

const fs = require('fs');
const { spawn } = require('child_process');
const path = require('path');

const filename = process.argv[2] || 'aprtag.zpl';
const printerName = 'ZDesigner ZT421-300dpi ZPL';

try {
  // Check if file exists
  if (!fs.existsSync(filename)) {
    console.error(`✗ File not found: ${filename}`);
    process.exit(1);
  }

  // Read ZPL file as binary
  const zplData = fs.readFileSync(filename);
  console.log(`📄 Read ${filename} (${zplData.length} bytes)`);

  // Convert to Base64 for PowerShell transmission
  const b64Data = zplData.toString('base64');

  console.log(`🖨️  Sending RAW ZPL to printer: ${printerName}...`);

  // Use PowerShell to send raw data via printer API
  const psScript = `
    Add-Type @"
    using System;
    using System.Runtime.InteropServices;
    public class Win32API {
      [DllImport("winspool.drv", CharSet = CharSet.Ansi)]
      public static extern bool OpenPrinter(string pPrinterName, out IntPtr phPrinter, IntPtr pDefault);
      [DllImport("winspool.drv")]
      public static extern bool ClosePrinter(IntPtr phPrinter);
      [DllImport("winspool.drv", CharSet = CharSet.Ansi)]
      public static extern bool StartDocPrinter(IntPtr phPrinter, int level, ref DOC_INFO_1 pDocInfo);
      [DllImport("winspool.drv")]
      public static extern bool EndDocPrinter(IntPtr phPrinter);
      [DllImport("winspool.drv")]
      public static extern bool WritePrinter(IntPtr phPrinter, IntPtr pBuf, uint cbBuf, out uint pcWritten);
      [StructLayout(LayoutKind.Sequential)]
      public struct DOC_INFO_1 {
        [MarshalAs(UnmanagedType.LPStr)]
        public string pDocName;
        [MarshalAs(UnmanagedType.LPStr)]
        public string pOutputFile;
        [MarshalAs(UnmanagedType.LPStr)]
        public string pDatatype;
      }
    }
"@

    [byte[]] $data = [Convert]::FromBase64String("${b64Data}")
    $printer = "${printerName}"
    [IntPtr] $hPrinter = [IntPtr]::Zero

    if ([Win32API]::OpenPrinter($printer, [ref]$hPrinter, [IntPtr]::Zero)) {
      $di = New-Object Win32API+DOC_INFO_1
      $di.pDocName = "ZPL Label"
      $di.pDatatype = "RAW"

      if ([Win32API]::StartDocPrinter($hPrinter, 1, [ref]$di)) {
        $ptr = [System.Runtime.InteropServices.Marshal]::AllocHGlobal($data.Length)
        [System.Runtime.InteropServices.Marshal]::Copy($data, 0, $ptr, $data.Length)
        [uint32]$written = 0
        [Win32API]::WritePrinter($hPrinter, $ptr, [uint32]$data.Length, [ref]$written)
        [Win32API]::EndDocPrinter($hPrinter)
        [System.Runtime.InteropServices.Marshal]::FreeHGlobal($ptr)
        Write-Host "Success"
      }
      [Win32API]::ClosePrinter($hPrinter)
    } else {
      Write-Host "Failed to open printer"
    }
  `;

  const ps = spawn('powershell.exe', ['-Command', psScript]);

  ps.stdout.on('data', (data) => {
    if (data.toString().includes('Success')) {
      console.log('✓ Raw ZPL sent to printer!');
    }
  });

  ps.stderr.on('data', (data) => {
    console.error(`Error: ${data}`);
  });

  ps.on('close', (code) => {
    if (code !== 0) {
      console.error(`Process exited with code ${code}`);
      process.exit(1);
    }
  });

} catch (error) {
  console.error(`✗ Error: ${error.message}`);
  process.exit(1);
}
