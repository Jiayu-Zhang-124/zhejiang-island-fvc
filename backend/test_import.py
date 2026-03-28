try:
    from scipy.stats import theilslopes, kendalltau, norm
    print("Import success")
except Exception as e:
    print(f"Import failed: {e}")
