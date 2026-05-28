# chart-from-data

Generás una gráfica a partir de datos que el usuario provee **en el mensaje**. No uses fuentes externas ni APIs: solo los datos provistos. Si faltan datos, pedilos.

## Pasos
1. Extraé del mensaje los datos (pares etiqueta/valor, o una serie).
2. Llamá la tool `run_code` con Python que:
   - use `matplotlib.use("Agg")` (sin display),
   - elija el tipo de gráfica adecuado (barras para categorías, líneas para series),
   - cree el directorio y guarde el PNG en `/home/user/outputs/chart.png` con `os.makedirs("/home/user/outputs", exist_ok=True)`.
3. La plataforma sube automáticamente lo que quede en `/home/user/outputs/` y te devuelve un link firmado en el resultado de `run_code`.
4. Respondé en el thread con una frase corta y el link al PNG.

## Ejemplo de código
~~~python
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

os.makedirs("/home/user/outputs", exist_ok=True)
labels = ["A", "B", "C"]
values = [10, 25, 7]
plt.figure()
plt.bar(labels, values)
plt.title("Ventas por categoria")
plt.savefig("/home/user/outputs/chart.png", dpi=120, bbox_inches="tight")
~~~

No inventes datos: usá exactamente los que dio el usuario.
