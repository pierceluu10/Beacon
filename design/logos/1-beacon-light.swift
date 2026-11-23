// Concept 2 "Beacon light": off-black tile, an amber light with a white cursor landing on it.
// Palette: tile #111214, light #FFB020, cursor #F7F7F5.
import AppKit

let out = CommandLine.arguments[1]
let tileColor = NSColor(srgbRed: 0.067, green: 0.071, blue: 0.078, alpha: 1)   // #111214
let lightColor = NSColor(srgbRed: 1, green: 0.69, blue: 0.125, alpha: 1)       // #FFB020
let cursorColor = NSColor(srgbRed: 0.969, green: 0.969, blue: 0.961, alpha: 1) // #F7F7F5

// Local units, cursor tip at the origin, y down. The light is centred a little up-left of the tip.
let arrow: [CGPoint] = [(0, 0), (0, 176), (43, 134), (74, 194), (104, 180), (73, 122), (132, 122)].map { CGPoint(x: $0.0, y: $0.1) }
let corner: CGFloat = 22, gap: CGFloat = 20
let lightCenter = CGPoint(x: -22, y: -22), lightRadius: CGFloat = 108
let glyphBounds = CGRect(x: -130, y: -130, width: 130 + 143, height: 130 + 205)
let glyphHeight: CGFloat = 270
let opticalShift = CGPoint(x: 0, y: -2)

func arrowPath(_ context: CGContext, width: CGFloat) {
    context.addLines(between: arrow); context.closePath()
    context.setLineWidth(width); context.drawPath(using: .fillStroke)
}

func glyph(_ context: CGContext) {
    context.setLineJoin(.round); context.setLineCap(.round)
    context.setFillColor(lightColor.cgColor)
    context.fillEllipse(in: CGRect(x: lightCenter.x - lightRadius, y: lightCenter.y - lightRadius, width: lightRadius * 2, height: lightRadius * 2))
    // The tile-coloured outline knocks the cursor out of the light so the two shapes read separately.
    context.setFillColor(tileColor.cgColor); context.setStrokeColor(tileColor.cgColor)
    arrowPath(context, width: corner + gap * 2)
    context.setFillColor(cursorColor.cgColor); context.setStrokeColor(cursorColor.cgColor)
    arrowPath(context, width: corner)
}

func render(size: Int) -> NSBitmapImageRep {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8, samplesPerPixel: 4,
                               hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    let context = NSGraphicsContext(bitmapImageRep: rep)!.cgContext
    context.translateBy(x: 0, y: CGFloat(size)); context.scaleBy(x: 1, y: -1)
    context.scaleBy(x: CGFloat(size) / 512, y: CGFloat(size) / 512)
    let tile = CGPath(roundedRect: CGRect(x: 50, y: 50, width: 412, height: 412), cornerWidth: 92, cornerHeight: 92, transform: nil)
    context.addPath(tile); context.setFillColor(tileColor.cgColor); context.fillPath()
    let scale = glyphHeight / glyphBounds.height
    context.translateBy(x: 256 + opticalShift.x - glyphBounds.midX * scale, y: 256 + opticalShift.y - glyphBounds.midY * scale)
    context.scaleBy(x: scale, y: scale)
    glyph(context)
    return rep
}

for (size, name) in [(512, "concept-2.png"), (64, "concept-2-64.png")] {
    try! render(size: size).representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: out).appendingPathComponent(name))
}
