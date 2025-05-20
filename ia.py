import google.generativeai as genai

genai.configure(api_key="AIzaSyATW_5IdxhFnFt03IShVOwyZDYsZmCx5S0")

for m in genai.list_models():
    if 'generateContent' in m.supported_generation_methods:
        print(m.name)
