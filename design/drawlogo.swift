// Run from the repo root: swift design/drawlogo.swift .
// Draws Beacon's mark: a pointer with two signal arcs at its tip. Writes logo.png (app tile) and mark.png (menu bar).
import AppKit

let out = CommandLine.arguments[1]
let tip = CGPoint(x: 230, y: 230)
let arrow: [CGPoint] = [(230, 230), (230, 450), (284, 398), (322, 470), (358, 454), (320, 383), (395, 383)].map { CGPoint(x: $0.0, y: $0.1) }
let arcs: [CGFloat] = [85, 155]
let arcWidth: CGFloat = 36, corner: CGFloat = 20
let arcStart: CGFloat = 205, arcEnd: CGFloat = 265
let bounds = CGRect(x: 56, y: 42, width: 350, height: 440)  // the glyph's ink, arcs' caps and the arrow's rounded corners included

func glyph(_ context: CGContext, arrowColor: NSColor, arcColor: NSColor) {
    context.setLineJoin(.round); context.setLineCap(.round)
    context.addLines(between: arrow); context.closePath()
    context.setFillColor(arrowColor.cgColor); context.setStrokeColor(arrowColor.cgColor); context.setLineWidth(corner)
    context.drawPath(using: .fillStroke)
    context.setStrokeColor(arcColor.cgColor); context.setLineWidth(arcWidth)
    for radius in arcs {
        // y points down here, so increasing angles turn clockwise, as the design measures them
        context.addArc(center: tip, radius: radius, startAngle: arcStart * .pi / 180, endAngle: arcEnd * .pi / 180, clockwise: false)
        context.strokePath()
    }
}

func render(width: Int, height: Int, _ draw: (CGContext) -> Void) -> NSBitmapImageRep {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: width, pixelsHigh: height, bitsPerSample: 8, samplesPerPixel: 4,
                               hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    let context = NSGraphicsContext(bitmapImageRep: rep)!.cgContext
    context.translateBy(x: 0, y: CGFloat(height)); context.scaleBy(x: 1, y: -1)  // y down, like the design's coordinates
    draw(context)
    return rep
}

func save(_ rep: NSBitmapImageRep, _ name: String) {
    try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: out).appendingPathComponent(name))
}

let amber = NSColor(srgbRed: 1, green: 0.69, blue: 0.125, alpha: 1)
let tile = render(width: 512, height: 512) { context in
    let square = CGPath(roundedRect: CGRect(x: 50, y: 50, width: 412, height: 412), cornerWidth: 92, cornerHeight: 92, transform: nil)
    context.addPath(square)
    context.setFillColor(NSColor(srgbRed: 0.075, green: 0.078, blue: 0.094, alpha: 1).cgColor)  // #131418, flat
    context.fillPath()
    let scale: CGFloat = 0.6
    context.translateBy(x: 256 - bounds.midX * scale, y: 256 - bounds.midY * scale); context.scaleBy(x: scale, y: scale)
    glyph(context, arrowColor: NSColor(srgbRed: 0.96, green: 0.96, blue: 0.97, alpha: 1), arcColor: amber)
}
save(tile, "logo.png")

let markHeight = 36  // 18 pt at 2x
let markScale = CGFloat(markHeight) / bounds.height
let mark = render(width: Int((bounds.width * markScale).rounded(.up)), height: markHeight) { context in
    context.scaleBy(x: markScale, y: markScale); context.translateBy(x: -bounds.minX, y: -bounds.minY)
    glyph(context, arrowColor: .black, arcColor: .black)
}
save(mark, "mark.png")
