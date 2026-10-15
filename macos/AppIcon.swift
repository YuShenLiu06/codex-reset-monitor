// Original app icon, drawn with AppKit and the same bell used by the wizard.
import AppKit

let destination = URL(fileURLWithPath:CommandLine.arguments[1],isDirectory:true)
try FileManager.default.createDirectory(at:destination,withIntermediateDirectories:true)
for size in [16,32,128,256,512] {
    for scale in [1,2] {
        let pixels = size * scale
        guard let bitmap = NSBitmapImageRep(bitmapDataPlanes:nil,pixelsWide:pixels,pixelsHigh:pixels,bitsPerSample:8,
            samplesPerPixel:4,hasAlpha:true,isPlanar:false,colorSpaceName:.deviceRGB,bytesPerRow:0,bitsPerPixel:0),
              let context = NSGraphicsContext(bitmapImageRep:bitmap) else { fatalError("Icon bitmap unavailable") }
        NSGraphicsContext.saveGraphicsState(); NSGraphicsContext.current = context
        context.cgContext.scaleBy(x:CGFloat(pixels)/1024,y:CGFloat(pixels)/1024)
        let tile = NSBezierPath(roundedRect:NSRect(x:76,y:76,width:872,height:872),xRadius:196,yRadius:196)
        let gradient = NSGradient(starting:NSColor(srgbRed:0.20,green:0.53,blue:1,alpha:1),
                                  ending:NSColor(srgbRed:0.11,green:0.28,blue:0.82,alpha:1))!
        gradient.draw(in:tile,angle:-90)
        guard let bell = NSImage(systemSymbolName:"bell.fill",accessibilityDescription:nil)?
            .withSymbolConfiguration(NSImage.SymbolConfiguration(pointSize:512,weight:.medium)) else { fatalError("Bell symbol unavailable") }
        let whiteBell = NSImage(size:bell.size)
        whiteBell.lockFocus(); bell.draw(at:.zero,from:.zero,operation:.sourceOver,fraction:1)
        NSColor.white.setFill(); NSRect(origin:.zero,size:bell.size).fill(using:.sourceAtop); whiteBell.unlockFocus()
        whiteBell.draw(in:NSRect(x:272,y:244,width:480,height:528),from:.zero,operation:.sourceOver,fraction:1)
        NSColor(srgbRed:0.29,green:0.91,blue:0.68,alpha:1).setFill()
        NSBezierPath(ovalIn:NSRect(x:672,y:646,width:156,height:156)).fill()
        NSGraphicsContext.restoreGraphicsState()
        let suffix = scale == 2 ? "@2x" : ""
        guard let png = bitmap.representation(using:.png,properties:[:]) else { fatalError("Icon PNG unavailable") }
        try png.write(to:destination.appendingPathComponent("icon_\(size)x\(size)\(suffix).png"))
    }
}
