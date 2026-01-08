# print-zpl.ps1 - Send raw ZPL to Zebra ZT421 printer via USB
# Usage: .\print-zpl.ps1 <zpl-file>
# Example: .\print-zpl.ps1 zpl_output\apriltag_tagStandard41h12_00650.zpl

param(
    [Parameter(Mandatory=$true, Position=0)]
    [string]$ZplFile,

    [Parameter(Mandatory=$false)]
    [string]$PrinterName = "ZDesigner ZT421-300dpi ZPL"
)

if (-not (Test-Path $ZplFile)) {
    Write-Error "File not found: $ZplFile"
    exit 1
}

# Add the RawPrinterHelper class using Windows API
Add-Type -TypeDefinition @"
using System;
using System.IO;
using System.Runtime.InteropServices;

public class RawPrinterHelper
{
    [StructLayout(LayoutKind.Sequential)]
    public struct DOCINFOA
    {
        [MarshalAs(UnmanagedType.LPStr)] public string pDocName;
        [MarshalAs(UnmanagedType.LPStr)] public string pOutputFile;
        [MarshalAs(UnmanagedType.LPStr)] public string pDataType;
    }

    [DllImport("winspool.drv", EntryPoint = "OpenPrinterA", SetLastError = true, CharSet = CharSet.Ansi)]
    public static extern bool OpenPrinter(string szPrinter, out IntPtr hPrinter, IntPtr pd);

    [DllImport("winspool.drv", EntryPoint = "ClosePrinter", SetLastError = true)]
    public static extern bool ClosePrinter(IntPtr hPrinter);

    [DllImport("winspool.drv", EntryPoint = "StartDocPrinterA", SetLastError = true, CharSet = CharSet.Ansi)]
    public static extern int StartDocPrinter(IntPtr hPrinter, int level, ref DOCINFOA di);

    [DllImport("winspool.drv", EntryPoint = "EndDocPrinter", SetLastError = true)]
    public static extern bool EndDocPrinter(IntPtr hPrinter);

    [DllImport("winspool.drv", EntryPoint = "StartPagePrinter", SetLastError = true)]
    public static extern bool StartPagePrinter(IntPtr hPrinter);

    [DllImport("winspool.drv", EntryPoint = "EndPagePrinter", SetLastError = true)]
    public static extern bool EndPagePrinter(IntPtr hPrinter);

    [DllImport("winspool.drv", EntryPoint = "WritePrinter", SetLastError = true)]
    public static extern bool WritePrinter(IntPtr hPrinter, byte[] pBytes, int dwCount, out int dwWritten);

    public static bool SendBytesToPrinter(string szPrinterName, byte[] bytes)
    {
        IntPtr hPrinter;
        DOCINFOA di = new DOCINFOA();
        di.pDocName = "ZPL Label";
        di.pDataType = "RAW";

        if (!OpenPrinter(szPrinterName, out hPrinter, IntPtr.Zero)) return false;
        if (StartDocPrinter(hPrinter, 1, ref di) == 0) { ClosePrinter(hPrinter); return false; }
        if (!StartPagePrinter(hPrinter)) { EndDocPrinter(hPrinter); ClosePrinter(hPrinter); return false; }

        int written;
        bool success = WritePrinter(hPrinter, bytes, bytes.Length, out written);

        EndPagePrinter(hPrinter);
        EndDocPrinter(hPrinter);
        ClosePrinter(hPrinter);
        return success;
    }
}
"@

# Read the ZPL file and send to printer
$bytes = [System.IO.File]::ReadAllBytes((Resolve-Path $ZplFile))
$result = [RawPrinterHelper]::SendBytesToPrinter($PrinterName, $bytes)

if ($result) {
    Write-Host "Success: Sent $ZplFile to $PrinterName"
} else {
    Write-Error "Failed to send $ZplFile to printer"
    exit 1
}
