// esbuild loads .css with the `text` loader so the stylesheet ships inside the
// bundle and the generated HTML stays a single file.
declare module "*.css" {
  const contents: string;
  export default contents;
}
